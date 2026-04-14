from __future__ import annotations
"""MySQL 查询工具：从 k8s secret 动态解析连接信息并执行查询。"""

import asyncio
import base64
import json
import shlex
from typing import Any

from nanobot.agent.tools.base import Tool


class MySQLQueryTool(Tool):
    """通过 `kubectl get secret` 获取 DB 连接信息后执行 MySQL 查询。"""

    @property
    def name(self) -> str:
        return "mysql_query"

    @property
    def description(self) -> str:
        return (
            "执行 MySQL 查询工具。"
            "先读取 tce 命名空间下 tdmq-rocketmq-db-binding secret，"
            "解码连接信息后执行查询，返回 query_result 与 query_error。"
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "database": {
                    "type": "string",
                    "description": "要查询的数据库名（库名）",
                },
                "table": {
                    "type": "string",
                    "description": "要查询的表名（用于参数约束和语义提示）",
                },
                "sql": {
                    "type": "string",
                    "description": "要执行的 SQL（建议只读查询）",
                },
                "namespace": {
                    "type": "string",
                    "description": "k8s secret 所在命名空间，默认 tce",
                    "default": "tce",
                },
                "secret_name": {
                    "type": "string",
                    "description": "k8s secret 名称，默认 tdmq-rocketmq-db-binding",
                    "default": "tdmq-rocketmq-db-binding",
                },
                "timeout": {
                    "type": "integer",
                    "description": "命令超时时间（秒），默认 60",
                    "default": 60,
                    "minimum": 1,
                    "maximum": 300,
                },
            },
            "required": ["database", "table", "sql"],
        }

    async def execute(
        self,
        database: str,
        table: str,
        sql: str,
        namespace: str = "tce",
        secret_name: str = "tdmq-rocketmq-db-binding",
        timeout: int = 60,
        **kwargs: Any,
    ) -> dict[str, Any]:
        commands: list[str] = []

        database = database.strip()
        table = table.strip()
        sql = sql.strip()
        namespace = namespace.strip() or "tce"
        secret_name = secret_name.strip() or "tdmq-rocketmq-db-binding"

        if not database:
            return {
                "result": "database 不能为空",
                "commands": [],
                "query_result": "",
                "query_error": "database 不能为空",
            }
        if not table:
            return {
                "result": "table 不能为空",
                "commands": [],
                "query_result": "",
                "query_error": "table 不能为空",
            }
        if not sql:
            return {
                "result": "sql 不能为空",
                "commands": [],
                "query_result": "",
                "query_error": "sql 不能为空",
            }

        if not self._is_readonly_query(sql):
            err = "仅允许只读 SQL（SELECT / SHOW / DESC / EXPLAIN / WITH）"
            return {
                "result": err,
                "commands": [],
                "query_result": "",
                "query_error": err,
            }

        # 步骤1：获取并解析 secret
        secret_cmd = f"kubectl get secret -n {shlex.quote(namespace)} {shlex.quote(secret_name)} -ojson"
        commands.append(secret_cmd)

        secret_stdout, secret_stderr, secret_rc, secret_exec_err = await self._run_shell(secret_cmd, timeout)
        if secret_exec_err:
            return {
                "result": f"获取 secret 失败: {secret_exec_err}",
                "commands": commands,
                "query_result": "",
                "query_error": secret_exec_err,
            }
        if secret_rc != 0:
            err_text = (secret_stderr or secret_stdout or "kubectl 命令执行失败").strip()
            return {
                "result": f"获取 secret 失败: {err_text}",
                "commands": commands,
                "query_result": "",
                "query_error": err_text,
            }

        conn, parse_err = self._parse_secret(secret_stdout, database)
        if parse_err:
            return {
                "result": f"解析 secret 失败: {parse_err}",
                "commands": commands,
                "query_result": "",
                "query_error": parse_err,
            }

        # 步骤2：执行 mysql 查询
        mysql_cmd = self._build_mysql_command(
            host=conn["host"],
            port=conn["port"],
            user=conn["user"],
            password=conn["password"],
            database=database,
            sql=sql,
            timeout=timeout,
        )
        commands.append(self._build_mysql_masked_command(
            host=conn["host"],
            port=conn["port"],
            user=conn["user"],
            database=database,
            sql=sql,
            timeout=timeout,
        ))

        query_stdout, query_stderr, query_rc, query_exec_err = await self._run_shell(mysql_cmd, timeout)

        if query_exec_err:
            return {
                "result": f"mysql 查询执行异常: {query_exec_err}",
                "commands": commands,
                "query_result": "",
                "query_error": query_exec_err,
                "db_connection": {
                    "host": conn["host"],
                    "port": conn["port"],
                    "user": conn["user"],
                    "database": database,
                    "table": table,
                },
            }

        if query_rc != 0:
            err_text = (query_stderr or query_stdout or "mysql 查询失败").strip()
            return {
                "result": f"mysql 查询失败: {err_text}",
                "commands": commands,
                "query_result": query_stdout.strip(),
                "query_error": err_text,
                "db_connection": {
                    "host": conn["host"],
                    "port": conn["port"],
                    "user": conn["user"],
                    "database": database,
                    "table": table,
                },
            }

        query_result = query_stdout.strip() if query_stdout.strip() else "(无输出)"
        warn = ""
        if table.lower() not in sql.lower():
            warn = "注意：传入 table 名未在 SQL 中出现。"

        result_text = "mysql 查询成功"
        if warn:
            result_text = f"{result_text}；{warn}"

        return {
            "result": result_text,
            "commands": commands,
            "query_result": query_result,
            "query_error": query_stderr.strip(),
            "db_connection": {
                "host": conn["host"],
                "port": conn["port"],
                "user": conn["user"],
                "database": database,
                "table": table,
            },
        }

    @staticmethod
    def _is_readonly_query(sql: str) -> bool:
        s = sql.strip().lstrip("(").strip().lower()
        return s.startswith(("select", "show", "desc", "describe", "explain", "with"))

    @staticmethod
    def _b64decode(value: str | None) -> str:
        if not value:
            return ""
        try:
            return base64.b64decode(value).decode("utf-8", errors="replace").strip()
        except Exception:
            return ""

    def _parse_secret(self, secret_json: str, database: str) -> tuple[dict[str, str] | None, str | None]:
        try:
            data = json.loads(secret_json)
        except (json.JSONDecodeError, ValueError) as e:
            return None, f"secret JSON 解析失败: {e}"

        encoded = data.get("data") or {}
        if not isinstance(encoded, dict):
            return None, "secret.data 字段不存在或格式不正确"

        host = self._b64decode(encoded.get("host"))
        port = self._b64decode(encoded.get("port"))
        user = self._b64decode(encoded.get("user"))
        password = self._b64decode(encoded.get("pass"))

        db_names_text = self._b64decode(encoded.get("db_name_list"))
        db_names: list[str] = []
        if db_names_text:
            try:
                parsed = json.loads(db_names_text)
                if isinstance(parsed, list):
                    db_names = [str(x) for x in parsed]
            except (json.JSONDecodeError, ValueError):
                db_names = []

        if db_names and database not in db_names:
            return None, f"数据库 {database} 不在 secret 的 db_name_list 中，可选: {', '.join(db_names)}"

        # host / port 缺失时，回退到 dbNodes 的主节点
        if not host or not port:
            db_nodes_text = self._b64decode(encoded.get("dbNodes"))
            if db_nodes_text:
                try:
                    db_nodes = json.loads(db_nodes_text)
                    if isinstance(db_nodes, list) and db_nodes:
                        node = next((n for n in db_nodes if isinstance(n, dict) and n.get("master") == 1), db_nodes[0])
                        if isinstance(node, dict):
                            host = host or str(node.get("host") or "")
                            node_port = node.get("port")
                            if not port and node_port is not None:
                                port = str(node_port)
                except (json.JSONDecodeError, ValueError):
                    pass

        if not host:
            host = self._b64decode(encoded.get("ip")) or self._b64decode(encoded.get("ipv4"))

        if not port:
            port = "3306"

        if not host or not user or not password:
            return None, "连接信息不完整（需要 host/user/pass）"

        return {
            "host": host,
            "port": port,
            "user": user,
            "password": password,
        }, None

    @staticmethod
    def _build_mysql_command(
        host: str,
        port: str,
        user: str,
        password: str,
        database: str,
        sql: str,
        timeout: int,
    ) -> str:
        return (
            f"MYSQL_PWD={shlex.quote(password)} "
            f"mysql --connect-timeout={int(timeout)} "
            f"-h {shlex.quote(host)} "
            f"-P {shlex.quote(str(port))} "
            f"-u {shlex.quote(user)} "
            f"-D {shlex.quote(database)} "
            f"--default-character-set=utf8mb4 "
            f"-e {shlex.quote(sql)}"
        )

    @staticmethod
    def _build_mysql_masked_command(
        host: str,
        port: str,
        user: str,
        database: str,
        sql: str,
        timeout: int,
    ) -> str:
        return (
            "MYSQL_PWD=****** "
            f"mysql --connect-timeout={int(timeout)} "
            f"-h {shlex.quote(host)} "
            f"-P {shlex.quote(str(port))} "
            f"-u {shlex.quote(user)} "
            f"-D {shlex.quote(database)} "
            "--default-character-set=utf8mb4 "
            f"-e {shlex.quote(sql)}"
        )

    @staticmethod
    async def _run_shell(command: str, timeout: int) -> tuple[str, str, int, str | None]:
        try:
            process = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout)
            except asyncio.TimeoutError:
                process.kill()
                return "", "", -1, f"命令执行超时（{timeout}秒）"

            return (
                stdout.decode("utf-8", errors="replace") if stdout else "",
                stderr.decode("utf-8", errors="replace") if stderr else "",
                process.returncode,
                None,
            )
        except Exception as e:
            return "", "", -1, str(e)
