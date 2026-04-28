"""Skill Agent 工具層（MCP 版本）。

工具的 schema 與執行皆透過 MCP client 與 mcp_server 互動。
本模組負責：
- 將 MCP 的 tool 定義轉成 OpenAI function calling schema
- 將模型回傳的 tool_calls 派送給 MCP client 執行
- 解析模型的 tool_calls 結構（標準化 arguments）
"""
from __future__ import annotations

import json
import logging
from typing import Any

from mcp_client import MCPStdioClient

logger = logging.getLogger(__name__)

# 不暴露給 LLM 的內部工具（client 端用來建立 skill registry 索引）
_INTERNAL_TOOL_NAMES = {"list_skills"}


def mcp_tools_to_openai_schemas(
    mcp_tools: list[dict[str, Any]],
    exclude: set[str] | None = None,
) -> list[dict[str, Any]]:
    """將 MCP 的 tool 定義列表轉成 OpenAI function calling schema。

    MCP 格式: {name, description, inputSchema}
    OpenAI 格式: {"type": "function",
                  "function": {name, description, parameters}}

    Args:
        mcp_tools: MCP server 回傳的 tools 列表。
        exclude: 不轉換的工具名稱集合（預設排除內部工具）。

    Returns:
        OpenAI tools 格式的列表，可直接放入 chat.completions 請求。
    """
    if exclude is None:
        exclude = _INTERNAL_TOOL_NAMES

    schemas = []
    for tool in mcp_tools:
        name = tool.get("name", "")
        if name in exclude:
            continue
        schemas.append({
            "type": "function",
            "function": {
                "name": name,
                "description": tool.get("description", ""),
                "parameters": tool.get(
                    "inputSchema",
                    {"type": "object", "properties": {}},
                ),
            },
        })
    return schemas


class ToolExecutor:
    """工具執行分派器（MCP 後端）。

    將模型回傳的 tool_calls 透過 MCP client 送到 server 執行，
    並回傳合併後的 text content。

    Attributes:
        client: 已連線的 MCPStdioClient。
    """

    def __init__(self, client: MCPStdioClient) -> None:
        """初始化。

        Args:
            client: 已完成 initialize 握手的 MCPStdioClient。
        """
        self.client = client

    def execute(
        self, tool_name: str, arguments: dict[str, Any]
    ) -> str:
        """執行指定工具並回傳結果字串。

        Args:
            tool_name: 工具名稱。
            arguments: 工具參數字典。

        Returns:
            MCP server 回傳的 text content（多段時以換行串接）。
            執行例外時回傳錯誤訊息字串，不向上拋出。
        """
        try:
            return self.client.call_tool(tool_name, arguments)
        except Exception as e:
            msg = f"工具 {tool_name} 執行失敗: {e}"
            logger.error(msg)
            return msg


def parse_tool_calls(
    tool_calls_raw: list[dict],
) -> list[dict[str, Any]]:
    """解析模型回傳的 tool_calls。

    處理 arguments 可能是 str 或 dict 的情況，
    以及 Qwen3 偶爾回傳格式異常的狀況。

    Args:
        tool_calls_raw: 模型回傳的 tool_calls 列表。

    Returns:
        標準化後的 tool call 列表，每項包含
        id、name、arguments（dict）。
    """
    parsed = []
    for tc in tool_calls_raw:
        func = tc.get("function", {})
        name = func.get("name", "")
        raw_args = func.get("arguments", "{}")

        # arguments 可能是 JSON 字串或已解析的 dict
        if isinstance(raw_args, str):
            try:
                arguments = json.loads(raw_args)
            except json.JSONDecodeError:
                logger.warning(
                    "工具參數 JSON 解析失敗: %s", raw_args
                )
                arguments = {}
        else:
            arguments = raw_args

        parsed.append({
            "id": tc.get("id", ""),
            "name": name,
            "arguments": arguments,
        })

    return parsed
