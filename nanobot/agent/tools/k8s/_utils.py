"""k8s 工具集共享的辅助函数。"""

import asyncio


async def run_command(command: str, timeout: int = 60) -> str:
    """执行 shell 命令并返回输出。"""
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
            return f"Error: 命令执行超时（{timeout}秒）"

        output_parts = []
        if stdout:
            output_parts.append(stdout.decode("utf-8", errors="replace"))
        if stderr:
            stderr_text = stderr.decode("utf-8", errors="replace")
            if stderr_text.strip():
                output_parts.append(f"STDERR:\n{stderr_text}")
        if process.returncode != 0:
            output_parts.append(f"\nExit code: {process.returncode}")

        return "\n".join(output_parts) if output_parts else "(无输出)"
    except Exception as e:
        return f"Error: {str(e)}"
