"""Skill MCP Server。

以 JSON-RPC 2.0 over stdio 實作 MCP 協定，將 SkillManager
的能力 (list_skills, read_skill, read_skill_reference)
對外暴露為 MCP tools。

啟動方式:
    python mcp_server.py [--skills-dir PATH]

協定參考:
- https://modelcontextprotocol.io/specification
- 訊息格式: 一行一個 JSON-RPC 物件 (line-delimited JSON over stdio)
- stdout 僅輸出 JSON-RPC 訊息，所有 log 走 stderr
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from typing import Any, Callable

import config
from skill_manager import SkillManager

# ============================================================
# Logging：一律送到 stderr，避免污染 stdout 的 JSON-RPC 通道
# ============================================================
logging.basicConfig(
    stream=sys.stderr,
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] mcp_server: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("mcp_server")


# ============================================================
# 協定常數
# ============================================================
PROTOCOL_VERSION = "2024-11-05"
SERVER_NAME = "skill-mcp-server"
SERVER_VERSION = "0.1.0"

# JSON-RPC 錯誤碼 (節錄自規範)
PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603


# ============================================================
# Tool 定義 (MCP 格式：name + description + inputSchema)
# ============================================================
TOOL_DEFINITIONS: list[dict[str, Any]] = [
    {
        "name": "list_skills",
        "description": (
            "列出所有已註冊的 skills（含 name 與 description）。"
            "回傳值為 JSON 字串，格式為 "
            "[{\"name\": str, \"description\": str}, ...]。"
            "用於 client 端建立 skill registry 索引。"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
    },
    {
        "name": "read_skill",
        "description": (
            "讀取指定 skill 的完整指引（SKILL.md）。"
            "當使用者的問題與某個 skill 的描述相符時，"
            "呼叫此工具來載入該 skill 的完整指引，"
            "然後嚴格按照指引中的流程和格式來回覆使用者。"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "skill_name": {
                    "type": "string",
                    "description": (
                        "skill 名稱，必須是 available_skills 中"
                        "列出的名稱之一"
                    ),
                },
            },
            "required": ["skill_name"],
            "additionalProperties": False,
        },
    },
    {
        "name": "read_skill_reference",
        "description": (
            "讀取 skill 的補充參考資料。"
            "當 SKILL.md 中提到需要參考 references/ 下的"
            "特定檔案時，使用此工具載入。"
            "不要主動猜測檔案名稱，"
            "請依據 SKILL.md 中的明確指示來呼叫。"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "skill_name": {
                    "type": "string",
                    "description": "skill 名稱",
                },
                "filename": {
                    "type": "string",
                    "description": (
                        "參考檔案名稱，例如 antipatterns.md"
                    ),
                },
            },
            "required": ["skill_name", "filename"],
            "additionalProperties": False,
        },
    },
]


# ============================================================
# Server 主體
# ============================================================
class SkillMCPServer:
    """MCP Server 主體，封裝 SkillManager 並處理 JSON-RPC 訊息。"""

    def __init__(self, skills_dir: str) -> None:
        self.manager = SkillManager(skills_dir)
        self._initialized = False
        self._handlers: dict[str, Callable[[dict], Any]] = {
            "initialize": self._handle_initialize,
            "ping": self._handle_ping,
            "tools/list": self._handle_tools_list,
            "tools/call": self._handle_tools_call,
        }
        # 沒有實質效果但可避免 client 報錯的 method
        self._notifications: set[str] = {
            "notifications/initialized",
            "notifications/cancelled",
        }
        logger.info(
            "SkillMCPServer 啟動 (skills_dir=%s, skills=%d)",
            skills_dir,
            len(self.manager.registry),
        )

    # ---- handler ----
    def _handle_initialize(self, params: dict) -> dict:
        client_info = params.get("clientInfo", {})
        logger.info(
            "initialize: client=%s/%s",
            client_info.get("name", "?"),
            client_info.get("version", "?"),
        )
        self._initialized = True
        return {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {
                "tools": {"listChanged": False},
            },
            "serverInfo": {
                "name": SERVER_NAME,
                "version": SERVER_VERSION,
            },
        }

    def _handle_ping(self, params: dict) -> dict:
        return {}

    def _handle_tools_list(self, params: dict) -> dict:
        return {"tools": TOOL_DEFINITIONS}

    def _handle_tools_call(self, params: dict) -> dict:
        name = params.get("name", "")
        args = params.get("arguments", {}) or {}
        logger.info("tools/call: %s args=%s", name, args)

        try:
            text = self._dispatch_tool(name, args)
            is_error = False
        except _ToolNotFound:
            text = f"未知的工具: {name}"
            is_error = True
        except Exception as e:
            logger.exception("tool 執行例外")
            text = f"工具 {name} 執行失敗: {e}"
            is_error = True

        return {
            "content": [{"type": "text", "text": text}],
            "isError": is_error,
        }

    def _dispatch_tool(self, name: str, args: dict) -> str:
        if name == "list_skills":
            skills = self.manager.list_skills()
            return json.dumps(skills, ensure_ascii=False)
        if name == "read_skill":
            return self.manager.read_skill(
                args.get("skill_name", "")
            )
        if name == "read_skill_reference":
            return self.manager.read_reference(
                args.get("skill_name", ""),
                args.get("filename", ""),
            )
        raise _ToolNotFound(name)

    # ---- 主迴圈 ----
    def serve(self) -> None:
        """讀取 stdin 的 JSON-RPC 訊息直到 EOF。"""
        for raw in sys.stdin:
            line = raw.strip()
            if not line:
                continue
            self._process_line(line)

    def _process_line(self, line: str) -> None:
        try:
            msg = json.loads(line)
        except json.JSONDecodeError as e:
            logger.warning("JSON 解析失敗: %s", e)
            self._send_error(None, PARSE_ERROR, f"Parse error: {e}")
            return

        method = msg.get("method")
        msg_id = msg.get("id")
        params = msg.get("params") or {}

        # Notification (沒有 id) → 不回應
        if msg_id is None:
            if method in self._notifications:
                logger.debug("notification: %s", method)
            else:
                logger.warning("未知的 notification: %s", method)
            return

        handler = self._handlers.get(method)
        if handler is None:
            self._send_error(
                msg_id,
                METHOD_NOT_FOUND,
                f"Method not found: {method}",
            )
            return

        try:
            result = handler(params)
        except Exception as e:
            logger.exception("handler 例外: %s", method)
            self._send_error(msg_id, INTERNAL_ERROR, str(e))
            return

        self._send_result(msg_id, result)

    # ---- 輸出 ----
    @staticmethod
    def _write(payload: dict) -> None:
        sys.stdout.write(
            json.dumps(payload, ensure_ascii=False) + "\n"
        )
        sys.stdout.flush()

    def _send_result(self, msg_id: Any, result: Any) -> None:
        self._write({
            "jsonrpc": "2.0",
            "id": msg_id,
            "result": result,
        })

    def _send_error(
        self, msg_id: Any, code: int, message: str
    ) -> None:
        self._write({
            "jsonrpc": "2.0",
            "id": msg_id,
            "error": {"code": code, "message": message},
        })


class _ToolNotFound(Exception):
    """指定的工具不存在於本 server。"""


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Skill MCP Server")
    parser.add_argument(
        "--skills-dir",
        default=os.getenv(
            "SKILL_AGENT_SKILLS_DIR", config.SKILLS_DIR
        ),
        help="skills 根目錄 (預設來自 config / 環境變數)",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = _parse_args(argv if argv is not None else sys.argv[1:])
    server = SkillMCPServer(args.skills_dir)
    try:
        server.serve()
    except KeyboardInterrupt:
        logger.info("server 中止 (KeyboardInterrupt)")


if __name__ == "__main__":
    main()
