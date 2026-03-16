"""Skill Agent 工具定義。

定義 OpenAI function calling 格式的工具 schema，
以及工具執行分派器。不依賴任何框架。
"""
import json
import logging
from typing import Any

from skill_manager import SkillManager

logger = logging.getLogger(__name__)

# --- OpenAI Tool Schema 定義 ---

TOOL_READ_SKILL = {
    "type": "function",
    "function": {
        "name": "read_skill",
        "description": (
            "讀取指定 skill 的完整指引（SKILL.md）。"
            "當使用者的問題與某個 skill 的描述相符時，"
            "呼叫此工具來載入該 skill 的完整指引，"
            "然後嚴格按照指引中的流程和格式來回覆使用者。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "skill_name": {
                    "type": "string",
                    "description": (
                        "skill 名稱，必須是 "
                        "available_skills 中列出的名稱之一"
                    ),
                },
            },
            "required": ["skill_name"],
        },
    },
}

TOOL_READ_REFERENCE = {
    "type": "function",
    "function": {
        "name": "read_skill_reference",
        "description": (
            "讀取 skill 的補充參考資料。"
            "當 SKILL.md 中提到需要參考 references/ 下的"
            "特定檔案時，使用此工具載入。"
            "不要主動猜測檔案名稱，"
            "請依據 SKILL.md 中的明確指示來呼叫。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "skill_name": {
                    "type": "string",
                    "description": "skill 名稱",
                },
                "filename": {
                    "type": "string",
                    "description": (
                        "參考檔案名稱，"
                        "如 antipatterns.md"
                    ),
                },
            },
            "required": ["skill_name", "filename"],
        },
    },
}


def get_tool_schemas() -> list[dict]:
    """取得所有工具的 OpenAI function calling schema。

    Returns:
        工具定義列表，可直接放入 API 請求的 tools 欄位。
    """
    return [TOOL_READ_SKILL, TOOL_READ_REFERENCE]


class ToolExecutor:
    """工具執行分派器。

    將模型回傳的 tool_calls 對應到實際的函式執行。

    Attributes:
        manager: SkillManager 實例。
    """

    def __init__(self, manager: SkillManager) -> None:
        """初始化。

        Args:
            manager: 已初始化的 SkillManager。
        """
        self.manager = manager
        self._dispatch = {
            "read_skill": self._exec_read_skill,
            "read_skill_reference": self._exec_read_reference,
        }

    def execute(
        self, tool_name: str, arguments: dict[str, Any]
    ) -> str:
        """執行指定工具並回傳結果字串。

        Args:
            tool_name: 工具名稱。
            arguments: 工具參數字典。

        Returns:
            工具執行結果的文字內容。
        """
        handler = self._dispatch.get(tool_name)
        if handler is None:
            msg = f"未知的工具: {tool_name}"
            logger.warning(msg)
            return msg
        try:
            return handler(arguments)
        except Exception as e:
            msg = f"工具 {tool_name} 執行失敗: {e}"
            logger.error(msg)
            return msg

    def _exec_read_skill(self, args: dict) -> str:
        """執行 read_skill。"""
        skill_name = args.get("skill_name", "")
        logger.info("載入 skill: %s", skill_name)
        return self.manager.read_skill(skill_name)

    def _exec_read_reference(self, args: dict) -> str:
        """執行 read_skill_reference。"""
        skill_name = args.get("skill_name", "")
        filename = args.get("filename", "")
        logger.info(
            "載入 reference: %s/%s", skill_name, filename
        )
        return self.manager.read_reference(
            skill_name, filename
        )


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