from __future__ import annotations
"""MySQL 查询工具：从 k8s secret 动态解析连接信息并执行查询。"""

import asyncio
import base64
import json
import shlex
from typing import Any

from nanobot.agent.tools.base import Tool


class MySQLQueryToolBySecret(Tool):
    """通过 `kubectl get secret` 获取 DB 连接信息后执行 MySQL 查询。"""

    @property
    def name(self) -> str:
        return "mysql_query_by_secret"

    @property
    def description(self) -> str:
        return (
            "执行 MySQL 查询工具。"
            "默认先读取 default 命名空间下 XXXX secret，"
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
                    "description": "k8s secret 所在命名空间，默认 default",
                    "default": "default",
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
                "max_output_rows": {
                    "type": "integer",
                    "description": "最大输出条数，默认 10，范围 1-1000",
                    "default": 10,
                    "minimum": 1,
                    "maximum": 1000,
                },
            },
            "required": ["database", "table", "sql"],
        }

    async def execute(
        self,
        database: str,
        table: str,
        sql: str,
        namespace: str = "default",
        secret_name: str = "tdmq-rocketmq-db-binding",
        timeout: int = 60,
        max_output_rows: int = 10,
        **kwargs: Any,
    ) -> dict[str, Any]:
        commands: list[str] = []

        database = database.strip()
        table = table.strip()
        sql = sql.strip()
        namespace = namespace.strip() or "default"
        secret_name = secret_name.strip() or "tdmq-rocketmq-db-binding"
        max_output_rows = max(1, min(int(max_output_rows), 1000))

        if not database:
            return {
                "result": "database 不能为空",
                "commands": [],
                "query_result": [],
                "query_error": "database 不能为空",
            }
        if not table:
            return {
                "result": "table 不能为空",
                "commands": [],
                "query_result": [],
                "query_error": "table 不能为空",
            }
        if not sql:
            return {
                "result": "sql 不能为空",
                "commands": [],
                "query_result": [],
                "query_error": "sql 不能为空",
            }

        if not self._is_readonly_query(sql):
            err = "仅允许只读 SQL（SELECT / SHOW / DESC / EXPLAIN / WITH）"
            return {
                "result": err,
                "commands": [],
                "query_result": [],
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
                "query_result": [],
                "query_error": secret_exec_err,
            }
        if secret_rc != 0:
            err_text = (secret_stderr or secret_stdout or "kubectl 命令执行失败").strip()
            return {
                "result": f"获取 secret 失败: {err_text}",
                "commands": commands,
                "query_result": [],
                "query_error": err_text,
            }

        conn, parse_err = self._parse_secret(secret_stdout, database)
        if parse_err:
            return {
                "result": f"解析 secret 失败: {parse_err}",
                "commands": commands,
                "query_result": [],
                "query_error": parse_err,
            }

        # 步骤2：使用 Python MySQL 客户端执行查询
        try:
            rows, total_rows = await self._query_with_mysql_client(
                host=conn["host"],
                port=conn["port"],
                user=conn["user"],
                password=conn["password"],
                database=database,
                sql=sql,
                timeout=timeout,
                max_output_rows=max_output_rows,
            )
        except Exception as e:
            err_text = str(e)
            return {
                "result": f"mysql 查询失败: {err_text}",
                "commands": commands,
                "query_result": [],
                "query_error": err_text,
                "db_connection": {
                    "host": conn["host"],
                    "port": conn["port"],
                    "user": conn["user"],
                    "database": database,
                    "table": table,
                },
            }

        warn = ""
        if table.lower() not in sql.lower():
            warn = "注意：传入 table 名未在 SQL 中出现。"

        result_text = "mysql 查询成功"
        if total_rows > max_output_rows:
            result_text = f"{result_text}；结果共 {total_rows} 条，已截断为前 {max_output_rows} 条"
        if warn:
            result_text = f"{result_text}；{warn}" if result_text else warn

        return {
            "result": result_text,
            "commands": commands,
            "query_result": rows,
            "query_error": "",
            "total_rows": total_rows,
            "max_output_rows": max_output_rows,
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

    async def _query_with_mysql_client(
        self,
        host: str,
        port: str,
        user: str,
        password: str,
        database: str,
        sql: str,
        timeout: int,
        max_output_rows: int,
    ) -> tuple[list[dict[str, Any]], int]:
        def _run_query() -> tuple[list[dict[str, Any]], int]:
            try:
                import importlib
                pymysql_mod = importlib.import_module("pymysql")
                dict_cursor = getattr(getattr(pymysql_mod, "cursors"), "DictCursor")
            except Exception as e:
                raise RuntimeError(
                    f"缺少 Python MySQL 客户端依赖 pymysql: {e}。请先安装: pip install pymysql"
                )

            conn = pymysql_mod.connect(
                host=host,
                port=int(port),
                user=user,
                password=password,
                database=database,
                charset="utf8mb4",
                cursorclass=dict_cursor,
                connect_timeout=int(timeout),
                read_timeout=int(timeout),
                write_timeout=int(timeout),
                autocommit=True,
            )
            try:
                with conn.cursor() as cursor:
                    cursor.execute(sql)
                    all_rows = cursor.fetchall() or []
                    rows = [dict(item) for item in all_rows[:max_output_rows]]
                    return rows, len(all_rows)
            finally:
                conn.close()

        return await asyncio.to_thread(_run_query)

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
