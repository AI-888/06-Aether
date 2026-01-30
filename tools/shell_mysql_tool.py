# tools/shell_mysql_tool.py
"""
RocketMQ Agent 工具
- 执行 Shell 命令
- 查询 MySQL
"""

import subprocess
from typing import Optional, List

import mysql.connector


# -----------------------------
# Shell 命令工具
# -----------------------------


def run_shell(cmd: str, timeout: Optional[int] = 10) -> str:
    """执行 shell 命令并返回 stdout"""
    try:
        result = subprocess.run(
            cmd, shell=True, capture_output=True, text=True, timeout=timeout
        )
        return result.stdout.strip()
    except subprocess.TimeoutExpired:
        return f"Command timed out after {timeout} seconds"
    except Exception as e:
        return f"Command failed: {str(e)}"


# -----------------------------
# MySQL 查询工具
# -----------------------------


def query_mysql(
        host: str, port: int, user: str, password: str, database: str, sql: str
) -> List[dict]:
    """执行 MySQL 查询并返回结果列表"""
    try:
        conn = mysql.connector.connect(
            host=host, port=port, user=user, password=password, database=database
        )
        cursor = conn.cursor(dictionary=True)
        cursor.execute(sql)
        results = cursor.fetchall()
        cursor.close()
        conn.close()
        return results
    except mysql.connector.Error as err:
        return [{"error": str(err)}]


# -----------------------------
# 示例运行
# -----------------------------

if __name__ == "__main__":
    # Shell 示例
    out = run_shell("echo Hello RocketMQ")
    print(f"Shell Output: {out}")

    # MySQL 示例
    rows = query_mysql(
        host="127.0.0.1",
        port=3306,
        user="root",
        password="password",
        database="rmq",
        sql="SELECT * FROM broker_metrics ORDER BY timestamp DESC LIMIT 5;",
    )
    print(rows)
