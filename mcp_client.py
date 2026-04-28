"""MCP Client：以子行程 + stdio 與 MCP server 對話的同步 client。

設計目標：
- 不依賴官方 mcp SDK，僅使用標準函式庫
- 與既有同步的 SkillAgent 介面相容（不引入 asyncio）
- 支援 context manager 與顯式 close()，避免 zombie process

使用範例:
    from mcp_client import MCPStdioClient

    client = MCPStdioClient([sys.executable, "mcp_server.py"])
    tools = client.list_tools()
    text = client.call_tool("read_skill", {"skill_name": "sql-style-guide"})
    client.close()
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

PROTOCOL_VERSION = "2024-11-05"
CLIENT_NAME = "skill-agent-client"
CLIENT_VERSION = "0.1.0"


class MCPClientError(RuntimeError):
    """MCP client 與 server 互動失敗時拋出。"""


class MCPStdioClient:
    """以 subprocess+stdio 通訊的同步 MCP client。

    啟動時會 spawn server 子行程、執行 initialize 握手，
    並提供 list_tools() / call_tool() 等高階介面。

    Attributes:
        server_info: server 回傳的 serverInfo 字典。
        capabilities: server 宣告的 capabilities。
    """

    def __init__(
        self,
        command: list[str],
        cwd: str | Path | None = None,
        env: dict[str, str] | None = None,
        startup_timeout: float = 10.0,
    ) -> None:
        """啟動 MCP server 並完成 initialize 握手。

        Args:
            command: 用來執行 server 的命令參數列表，
                例如 [sys.executable, "mcp_server.py"]。
            cwd: server 子行程的工作目錄；None 表示繼承呼叫方。
            env: 環境變數覆寫；None 表示繼承呼叫方。
            startup_timeout: initialize 等待回應的逾時秒數。
        """
        self._command = command
        self._lock = threading.Lock()
        self._next_id = 0
        self._closed = False

        merged_env = os.environ.copy()
        if env:
            merged_env.update(env)

        logger.info("啟動 MCP server: %s", " ".join(command))
        self._proc = subprocess.Popen(
            command,
            cwd=str(cwd) if cwd else None,
            env=merged_env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            bufsize=1,  # line-buffered
        )

        # 在背景把 server 的 stderr 轉寄到本地 logger
        self._stderr_thread = threading.Thread(
            target=self._drain_stderr,
            daemon=True,
            name="mcp-server-stderr",
        )
        self._stderr_thread.start()

        try:
            init_result = self._request(
                "initialize",
                {
                    "protocolVersion": PROTOCOL_VERSION,
                    "capabilities": {},
                    "clientInfo": {
                        "name": CLIENT_NAME,
                        "version": CLIENT_VERSION,
                    },
                },
                timeout=startup_timeout,
            )
            self._notify("notifications/initialized")
        except Exception:
            self.close()
            raise

        self.server_info = init_result.get("serverInfo", {})
        self.capabilities = init_result.get("capabilities", {})
        logger.info(
            "MCP server 已連線: %s/%s (protocol=%s)",
            self.server_info.get("name", "?"),
            self.server_info.get("version", "?"),
            init_result.get("protocolVersion", "?"),
        )

    # ---- 高階 API ----
    def list_tools(self) -> list[dict[str, Any]]:
        """取得 server 暴露的所有 tool 定義（MCP 格式）。"""
        result = self._request("tools/list")
        return result.get("tools", [])

    def call_tool(
        self,
        name: str,
        arguments: dict[str, Any] | None = None,
    ) -> str:
        """呼叫指定工具，回傳 text content 的串接字串。

        Args:
            name: 工具名稱。
            arguments: 工具參數字典。

        Returns:
            所有 type=text 的 content 串接後的字串。
            若 server 回報 isError=True，會在前面加上錯誤標記。
        """
        result = self._request(
            "tools/call",
            {"name": name, "arguments": arguments or {}},
        )
        text_parts: list[str] = []
        for item in result.get("content", []):
            if item.get("type") == "text":
                text_parts.append(item.get("text", ""))
        text = "\n".join(text_parts)
        if result.get("isError"):
            logger.warning("MCP tool '%s' 回報錯誤: %s", name, text)
        return text

    def ping(self) -> None:
        """送一個 ping 確認連線是否健康。"""
        self._request("ping")

    # ---- 生命週期 ----
    def close(self) -> None:
        """關閉與 server 的連線並終止子行程。"""
        if self._closed:
            return
        self._closed = True
        try:
            if self._proc.stdin and not self._proc.stdin.closed:
                self._proc.stdin.close()
        except Exception:
            pass
        try:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                logger.warning("server 未在 5 秒內結束，強制 kill")
                self._proc.kill()
                self._proc.wait(timeout=2)
        except Exception as e:
            logger.warning("關閉 server 時發生例外: %s", e)
        logger.info("MCP server 已關閉")

    def __enter__(self) -> "MCPStdioClient":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:
            pass

    # ---- 底層 JSON-RPC ----
    def _request(
        self,
        method: str,
        params: dict | None = None,
        timeout: float | None = None,
    ) -> dict:
        """送出 request 並阻塞等回應。"""
        with self._lock:
            self._next_id += 1
            req_id = self._next_id
            payload: dict[str, Any] = {
                "jsonrpc": "2.0",
                "id": req_id,
                "method": method,
            }
            if params is not None:
                payload["params"] = params
            self._write_message(payload)
            resp = self._read_message(timeout)

        if resp.get("id") != req_id:
            raise MCPClientError(
                f"id 不一致: 預期 {req_id}, 收到 {resp.get('id')}"
            )
        if "error" in resp:
            err = resp["error"]
            raise MCPClientError(
                f"server 回傳錯誤 ({err.get('code')}): "
                f"{err.get('message')}"
            )
        return resp.get("result", {}) or {}

    def _notify(
        self, method: str, params: dict | None = None
    ) -> None:
        """送出 notification (沒有 id, 不期待回應)。"""
        payload: dict[str, Any] = {
            "jsonrpc": "2.0",
            "method": method,
        }
        if params is not None:
            payload["params"] = params
        with self._lock:
            self._write_message(payload)

    def _write_message(self, payload: dict) -> None:
        if self._proc.stdin is None or self._proc.stdin.closed:
            raise MCPClientError("server stdin 已關閉")
        line = json.dumps(payload, ensure_ascii=False) + "\n"
        try:
            self._proc.stdin.write(line)
            self._proc.stdin.flush()
        except BrokenPipeError as e:
            raise MCPClientError(
                "與 server 的連線中斷 (BrokenPipe)"
            ) from e

    def _read_message(self, timeout: float | None) -> dict:
        # subprocess.PIPE 的 readline 沒有原生 timeout，
        # 用 thread + Event 模擬。實務上 server 都很快回應，
        # timeout 主要保護啟動握手。
        if timeout is None:
            line = self._proc.stdout.readline()
        else:
            line = self._readline_with_timeout(timeout)

        if not line:
            rc = self._proc.poll()
            raise MCPClientError(
                f"server 連線中斷 (returncode={rc})"
            )
        try:
            return json.loads(line)
        except json.JSONDecodeError as e:
            raise MCPClientError(
                f"server 回應不是合法 JSON: {line!r}"
            ) from e

    def _readline_with_timeout(self, timeout: float) -> str:
        result: list[str] = []
        done = threading.Event()

        def _read() -> None:
            try:
                result.append(self._proc.stdout.readline())
            finally:
                done.set()

        t = threading.Thread(target=_read, daemon=True)
        t.start()
        if not done.wait(timeout):
            raise MCPClientError(
                f"等待 server 回應逾時 ({timeout}s)"
            )
        return result[0] if result else ""

    def _drain_stderr(self) -> None:
        """把 server 的 stderr 行轉寄到本地 logger.debug。"""
        stream = self._proc.stderr
        if stream is None:
            return
        for raw in stream:
            line = raw.rstrip("\n")
            if line:
                logger.debug("[server] %s", line)


def default_server_command() -> list[str]:
    """產生啟動內建 mcp_server.py 的命令列。"""
    server_path = Path(__file__).parent / "mcp_server.py"
    return [sys.executable, str(server_path)]
