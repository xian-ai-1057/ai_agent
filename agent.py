"""Skill Agent 核心模組。

使用原生 requests 呼叫 vLLM OpenAI 相容 API，
自行實作 ReAct Agent Loop（tool calling 迴圈）。

工具層改走 MCP：透過 MCPStdioClient 與 mcp_server 對談，
所有 skill metadata 與檔案讀取皆由 MCP server 提供。

System Prompt 從外部 .md 檔案載入，支援模組化組裝。
"""
import json
import logging
from pathlib import Path

import requests

import config
from mcp_client import MCPStdioClient, default_server_command
from tools import (
    ToolExecutor,
    mcp_tools_to_openai_schemas,
    parse_tool_calls,
)

logger = logging.getLogger(__name__)

# System Prompt 檔案預設路徑（相對於本模組）
_DEFAULT_PROMPT_PATH = (
    Path(__file__).parent / "prompts" / "system_prompt.md"
)

# Compaction 摘要指令
_COMPACTION_PROMPT = (
    "你是一個對話摘要助手。請為以下對話產生精簡摘要，"
    "目的是保留足夠的脈絡讓對話能在新的 context 中繼續。\n\n"
    "必須記錄：\n"
    "1. 使用者的主要需求和問題\n"
    "2. 已達成的共識、決定和結論\n"
    "3. 關鍵的技術細節、程式碼片段或約束條件\n"
    "4. 尚未完成的待辦事項或進行中的任務\n\n"
    "原則：\n"
    "- 簡潔但不遺漏關鍵資訊\n"
    "- 保留具體的數值、名稱、路徑等細節\n"
    "- 使用條列式組織，方便快速掃描\n"
    "- 不要加入你的評論，只記錄事實"
)


def load_prompt_template(
    path: str | Path | None = None,
) -> str:
    """從 .md 檔案載入 system prompt 模板。

    模板中可使用 {available_skills} 等佔位符，
    會在 build_system_prompt 時替換。

    Args:
        path: prompt 檔案路徑。
            若為 None 使用預設的 prompts/system_prompt.md。

    Returns:
        prompt 模板字串。

    Raises:
        FileNotFoundError: 檔案不存在。
    """
    if path is None:
        path = _DEFAULT_PROMPT_PATH
    path = Path(path)

    if not path.exists():
        raise FileNotFoundError(
            f"System prompt 檔案不存在: {path}"
        )

    text = path.read_text(encoding="utf-8")
    logger.info("載入 system prompt: %s (%d 字)", path, len(text))
    return text


def build_system_prompt(
    registry: "SkillRegistry",
    prompt_path: str | Path | None = None,
) -> str:
    """組裝完整的 system prompt。

    從檔案載入模板，注入動態內容（如 skill registry）。
    模板中支援的佔位符：
    - {available_skills}: 所有 skill 的 metadata XML

    Args:
        registry: 已快取 skill metadata 的 SkillRegistry。
        prompt_path: 自訂 prompt 檔案路徑（可選）。

    Returns:
        完整的 system prompt 字串。
    """
    template = load_prompt_template(prompt_path)
    registry_prompt = registry.build_registry_prompt()
    return template.format(
        available_skills=registry_prompt
    )


class SkillRegistry:
    """Skill metadata 快取，從 MCP server 取得後格式化供 prompt 使用。

    取代原本 SkillManager 對 agent / UI 暴露的 list_skills 與
    build_registry_prompt 介面。實際的 skill 內容讀取仍走
    MCP server 的 tool call。
    """

    def __init__(self, client: MCPStdioClient) -> None:
        """初始化並透過 MCP 取得 skill 列表。

        Args:
            client: 已連線的 MCPStdioClient。
        """
        self._client = client
        self._skills: list[dict[str, str]] = self._fetch_skills()

    def _fetch_skills(self) -> list[dict[str, str]]:
        """呼叫 MCP server 的 list_skills 工具並解析結果。"""
        try:
            text = self._client.call_tool("list_skills", {})
            data = json.loads(text) if text else []
        except Exception as e:
            logger.error("從 MCP 取得 skill 列表失敗: %s", e)
            return []

        if not isinstance(data, list):
            logger.warning(
                "list_skills 回傳格式不正確: %r", data
            )
            return []
        return data

    def list_skills(self) -> list[dict[str, str]]:
        """取得所有 skill 的 (name, description) 列表。"""
        return list(self._skills)

    def build_registry_prompt(self) -> str:
        """產生注入 system prompt 的 <available_skills> XML 區塊。"""
        if not self._skills:
            return ""

        entries = []
        for meta in self._skills:
            name = meta.get("name", "")
            desc = meta.get("description", "（無描述）")
            desc = " ".join(desc.split())
            entries.append(
                f"<skill>\n"
                f"  <name>{name}</name>\n"
                f"  <description>{desc}</description>\n"
                f"</skill>"
            )

        skills_xml = "\n".join(entries)
        return (
            f"<available_skills>\n"
            f"{skills_xml}\n"
            f"</available_skills>"
        )

    def refresh(self) -> None:
        """強制重新從 server 拉取 skill 列表。"""
        self._skills = self._fetch_skills()


def _call_api(
    messages: list[dict],
    tools: list[dict],
    enable_thinking: bool | None = None,
) -> dict:
    """呼叫 vLLM OpenAI 相容 API。

    Args:
        messages: 對話訊息列表。
        tools: 工具定義列表。
        enable_thinking: 是否啟用思考模式。
            None 表示使用 config 預設值。

    Returns:
        API 回應的 JSON 字典。

    Raises:
        requests.RequestException: 網路或 API 錯誤。
        ValueError: 回應格式異常。
    """
    url = f"{config.API_BASE_URL}/chat/completions"

    if enable_thinking is None:
        enable_thinking = config.ENABLE_THINKING

    payload = {
        "model": config.MODEL_NAME,
        "messages": messages,
        "temperature": config.TEMPERATURE,
        "top_p": config.TOP_P,
        "max_tokens": config.MAX_TOKENS,
        "chat_template_kwargs": {
            "enable_thinking": enable_thinking,
        },
    }

    # 只在有工具時附加 tools 參數
    if tools:
        payload["tools"] = tools
        payload["tool_choice"] = "auto"

    headers = {"Content-Type": "application/json"}

    logger.debug(
        "API 請求: model=%s, messages=%d, tools=%d",
        config.MODEL_NAME,
        len(messages),
        len(tools),
    )

    resp = requests.post(
        url,
        headers=headers,
        data=json.dumps(payload, ensure_ascii=False),
        timeout=config.REQUEST_TIMEOUT,
    )
    resp.raise_for_status()

    data = resp.json()

    # 檢查 API 錯誤
    if "error" in data:
        raise ValueError(f"API 錯誤: {data['error']}")

    return data


def _extract_response(api_response: dict) -> dict:
    """從 API 回應中提取 assistant message 和 usage。

    處理 Qwen3 可能回傳的 reasoning
    和 tool_calls，同時提取 token 用量。

    Args:
        api_response: API 回應 JSON。

    Returns:
        包含以下欄位的字典:
        - content: 回覆文字（可能為空字串）
        - reasoning: 思考過程（可能為 None）
        - tool_calls: 工具呼叫列表（可能為空列表）
        - raw_message: 原始 message 字典
        - usage: token 用量字典（prompt_tokens,
          completion_tokens, total_tokens）
    """
    choices = api_response.get("choices", [])
    usage = api_response.get("usage", {})

    if not choices:
        return {
            "content": "",
            "reasoning": None,
            "tool_calls": [],
            "raw_message": {},
            "usage": {
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "total_tokens": 0,
            },
        }

    message = choices[0].get("message", {})

    return {
        "content": message.get("content") or "",
        "reasoning": message.get("reasoning"),
        "tool_calls": message.get("tool_calls") or [],
        "raw_message": message,
        "usage": {
            "prompt_tokens": usage.get(
                "prompt_tokens", 0
            ),
            "completion_tokens": usage.get(
                "completion_tokens", 0
            ),
            "total_tokens": usage.get(
                "total_tokens", 0
            ),
        },
    }


class SkillAgent:
    """Skill Agent 主體。

    管理對話狀態、執行 ReAct 迴圈、自動 Compaction。
    工具層透過 MCPStdioClient 與 mcp_server 互動。

    Attributes:
        mcp_client: 已連線的 MCPStdioClient。
        manager: SkillRegistry，向後相容 SkillManager 的部分介面。
        executor: 透過 MCP 派送工具呼叫的 ToolExecutor。
        system_prompt: 組裝後的 system prompt。
        tool_schemas: OpenAI 格式的工具定義列表（已過濾內部工具）。
        messages: 對話歷史。
    """

    def __init__(
        self,
        skills_dir: str | None = None,
        prompt_path: str | Path | None = None,
        mcp_command: list[str] | None = None,
    ) -> None:
        """初始化 Agent。

        Args:
            skills_dir: skills 根目錄。若為 None，使用 config 預設值；
                會以環境變數 SKILL_AGENT_SKILLS_DIR 傳給 server 子行程。
            prompt_path: 自訂 system prompt 檔案路徑。
                若為 None 使用預設的 prompts/system_prompt.md。
            mcp_command: 自訂 MCP server 啟動命令；
                若為 None 使用內建的 mcp_server.py。
        """
        if skills_dir is None:
            skills_dir = config.SKILLS_DIR
        if mcp_command is None:
            mcp_command = default_server_command()

        # 啟動 MCP server 子行程並完成 initialize 握手
        self.mcp_client = MCPStdioClient(
            mcp_command,
            env={"SKILL_AGENT_SKILLS_DIR": skills_dir},
        )

        try:
            mcp_tools = self.mcp_client.list_tools()
            self.tool_schemas = mcp_tools_to_openai_schemas(
                mcp_tools
            )
            self.manager = SkillRegistry(self.mcp_client)
            self.executor = ToolExecutor(self.mcp_client)
            self.system_prompt = build_system_prompt(
                self.manager, prompt_path
            )
        except Exception:
            self.mcp_client.close()
            raise

        self.messages: list[dict] = []
        self._last_prompt_tokens: int = 0

        logger.info(
            "Agent 初始化完成 (模型=%s, skills=%d, MCP tools=%d)",
            config.MODEL_NAME,
            len(self.manager.list_skills()),
            len(self.tool_schemas),
        )

    def close(self) -> None:
        """關閉 MCP client 與 server 子行程。"""
        if hasattr(self, "mcp_client"):
            self.mcp_client.close()

    def __enter__(self) -> "SkillAgent":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:
            pass

    def reset(self) -> None:
        """重置對話歷史與 token 追蹤。"""
        self.messages = []
        self._last_prompt_tokens = 0
        logger.info("對話歷史已重置")

    # ---- Compaction 機制 ----

    def _needs_compaction(self) -> bool:
        """判斷是否需要執行 compaction。

        Returns:
            True 表示 prompt_tokens 已超過閾值且
            有足夠的訊息可供壓縮。
        """
        threshold = config.COMPACTION_THRESHOLD
        if threshold <= 0:
            return False
        return (
            self._last_prompt_tokens > threshold
            and len(self.messages) > 2
        )

    def _format_messages_for_summary(self) -> str:
        """將對話歷史格式化為可供摘要的文字。

        Returns:
            格式化後的對話文字。
        """
        parts = []
        for msg in self.messages:
            role_map = {
                "user": "使用者",
                "assistant": "助手",
            }
            role = role_map.get(msg["role"], msg["role"])
            content = msg.get("content", "")
            if content:
                parts.append(f"[{role}] {content}")
        return "\n\n".join(parts)

    def _compact_messages(self) -> dict:
        """執行 compaction：壓縮對話歷史為摘要。

        呼叫模型產生摘要，然後用摘要取代原本的
        完整對話歷史。關閉思考模式以節省 token。

        Returns:
            包含 compaction 資訊的字典:
            - summary: 摘要文字
            - original_tokens: 壓縮前的 prompt_tokens
            - original_messages: 壓縮前的訊息數量
        """
        original_count = len(self.messages)
        original_tokens = self._last_prompt_tokens

        logger.info(
            "觸發 Compaction: prompt_tokens=%d, "
            "閾值=%d, 訊息數=%d",
            original_tokens,
            config.COMPACTION_THRESHOLD,
            original_count,
        )

        conversation_text = (
            self._format_messages_for_summary()
        )

        summary_messages = [
            {
                "role": "system",
                "content": _COMPACTION_PROMPT,
            },
            {
                "role": "user",
                "content": (
                    "請摘要以下對話：\n\n"
                    f"{conversation_text}"
                ),
            },
        ]

        try:
            api_resp = _call_api(
                summary_messages,
                tools=[],
                enable_thinking=False,
            )
            result = _extract_response(api_resp)
            summary = result["content"]
        except Exception as e:
            logger.error("Compaction 失敗: %s", e)
            # 失敗時 fallback：保留最近 4 則訊息
            if len(self.messages) > 4:
                self.messages = self.messages[-4:]
            self._last_prompt_tokens = 0
            return {
                "summary": f"（壓縮失敗: {e}）",
                "original_tokens": original_tokens,
                "original_messages": original_count,
            }

        # 用摘要取代歷史訊息
        self.messages = [
            {
                "role": "user",
                "content": (
                    "[以下是之前對話的摘要]\n\n"
                    f"{summary}"
                ),
            },
            {
                "role": "assistant",
                "content": (
                    "了解，我已掌握前段對話的脈絡。"
                    "請繼續提問或交代任務。"
                ),
            },
        ]
        self._last_prompt_tokens = 0

        logger.info(
            "Compaction 完成: %d 則訊息 → 2 則摘要 "
            "(%d tokens → 待下次計算)",
            original_count,
            original_tokens,
        )

        return {
            "summary": summary,
            "original_tokens": original_tokens,
            "original_messages": original_count,
        }

    def chat(self, user_input: str) -> str:
        """處理一輪使用者輸入，回傳最終回覆。

        包含完整的 ReAct 迴圈：
        1. 檢查是否需要 compaction
        2. 將使用者訊息加入歷史
        3. 呼叫 API
        4. 若模型要求呼叫工具 → 執行 → 回傳 → 再呼叫
        5. 重複直到最終回覆（或達到最大輪次）

        Args:
            user_input: 使用者輸入文字。

        Returns:
            模型的最終回覆文字。
        """
        # 檢查是否需要 compaction
        if self._needs_compaction():
            self._compact_messages()

        # 加入使用者訊息
        self.messages.append({
            "role": "user",
            "content": user_input,
        })

        # 組裝完整 messages（system + history）
        full_messages = [
            {"role": "system", "content": self.system_prompt},
            *self.messages,
        ]

        # ReAct 迴圈
        for round_idx in range(config.MAX_TOOL_ROUNDS):
            logger.debug("ReAct 第 %d 輪", round_idx + 1)

            api_resp = _call_api(
                full_messages, self.tool_schemas
            )
            result = _extract_response(api_resp)

            # 追蹤 token 用量
            self._last_prompt_tokens = (
                result["usage"]["prompt_tokens"]
            )
            logger.debug(
                "Token 用量: prompt=%d, completion=%d",
                result["usage"]["prompt_tokens"],
                result["usage"]["completion_tokens"],
            )

            # 記錄思考過程（debug 用）
            if result["reasoning"]:
                logger.debug(
                    "思考過程: %s",
                    result["reasoning"][:200],
                )

            # 沒有 tool_calls → 最終回覆
            if not result["tool_calls"]:
                content = result["content"]
                self.messages.append({
                    "role": "assistant",
                    "content": content,
                })
                return content

            # 有 tool_calls → 執行工具
            # 先將 assistant 的 tool_calls 訊息加入歷史
            assistant_msg = self._build_assistant_tool_msg(
                result
            )
            full_messages.append(assistant_msg)

            # 解析並執行每個 tool call
            parsed_calls = parse_tool_calls(
                result["tool_calls"]
            )
            for tc in parsed_calls:
                logger.info(
                    "執行工具: %s(%s)",
                    tc["name"],
                    json.dumps(
                        tc["arguments"], ensure_ascii=False
                    )[:100],
                )

                tool_result = self.executor.execute(
                    tc["name"], tc["arguments"]
                )

                # 將工具結果加入 messages
                full_messages.append({
                    "role": "tool",
                    "tool_call_id": tc["id"],
                    "content": tool_result,
                })

        # 達到最大輪次，做一次不帶 tools 的呼叫
        # 強制模型產生最終回覆
        logger.warning(
            "達到最大工具呼叫輪次 (%d)，強制產生回覆",
            config.MAX_TOOL_ROUNDS,
        )
        api_resp = _call_api(full_messages, tools=[])
        result = _extract_response(api_resp)
        self._last_prompt_tokens = (
            result["usage"]["prompt_tokens"]
        )
        content = result["content"]
        self.messages.append({
            "role": "assistant",
            "content": content,
        })
        return content

    def chat_with_trace(
        self, user_input: str
    ) -> dict:
        """處理使用者輸入，回傳最終回覆及中間追蹤資訊。

        與 chat() 邏輯相同，但額外收集每一輪的思考過程、
        工具呼叫、工具結果和 compaction 事件，供 UI 展示。

        Args:
            user_input: 使用者輸入文字。

        Returns:
            字典，包含:
            - content: 最終回覆文字
            - trace: 追蹤步驟列表，每項為 dict，
              type 為 "thinking" / "tool_call" /
              "tool_result" / "compaction"
        """
        trace = []

        # 檢查是否需要 compaction
        if self._needs_compaction():
            compact_info = self._compact_messages()
            trace.append({
                "type": "compaction",
                "round": 0,
                "content": compact_info["summary"],
                "original_tokens": (
                    compact_info["original_tokens"]
                ),
                "original_messages": (
                    compact_info["original_messages"]
                ),
            })

        self.messages.append({
            "role": "user",
            "content": user_input,
        })

        full_messages = [
            {"role": "system", "content": self.system_prompt},
            *self.messages,
        ]

        for round_idx in range(config.MAX_TOOL_ROUNDS):
            api_resp = _call_api(
                full_messages, self.tool_schemas
            )
            result = _extract_response(api_resp)

            # 追蹤 token 用量
            self._last_prompt_tokens = (
                result["usage"]["prompt_tokens"]
            )

            if result["reasoning"]:
                trace.append({
                    "type": "thinking",
                    "round": round_idx + 1,
                    "content": result["reasoning"],
                })

            if not result["tool_calls"]:
                content = result["content"]
                self.messages.append({
                    "role": "assistant",
                    "content": content,
                })
                return {
                    "content": content,
                    "trace": trace,
                    "usage": result["usage"],
                }

            assistant_msg = self._build_assistant_tool_msg(
                result
            )
            full_messages.append(assistant_msg)

            parsed_calls = parse_tool_calls(
                result["tool_calls"]
            )
            for tc in parsed_calls:
                trace.append({
                    "type": "tool_call",
                    "round": round_idx + 1,
                    "name": tc["name"],
                    "arguments": tc["arguments"],
                })

                tool_result = self.executor.execute(
                    tc["name"], tc["arguments"]
                )

                trace.append({
                    "type": "tool_result",
                    "round": round_idx + 1,
                    "name": tc["name"],
                    "content": tool_result,
                })

                full_messages.append({
                    "role": "tool",
                    "tool_call_id": tc["id"],
                    "content": tool_result,
                })

        api_resp = _call_api(full_messages, tools=[])
        result = _extract_response(api_resp)
        self._last_prompt_tokens = (
            result["usage"]["prompt_tokens"]
        )
        content = result["content"]
        self.messages.append({
            "role": "assistant",
            "content": content,
        })
        return {
            "content": content,
            "trace": trace,
            "usage": result["usage"],
        }

    def _build_assistant_tool_msg(
        self, result: dict
    ) -> dict:
        """建構包含 tool_calls 的 assistant message。

        OpenAI 格式要求 assistant message 包含完整的
        tool_calls 結構，供後續 tool role 訊息對應。

        Args:
            result: _extract_response 的回傳值。

        Returns:
            符合 OpenAI 格式的 assistant message 字典。
        """
        msg = {
            "role": "assistant",
            "content": result["content"] or None,
            "tool_calls": result["tool_calls"],
        }
        return msg

    def get_compact_history(self) -> list[dict]:
        """取得精簡版對話歷史（僅 user/assistant）。

        用於外部儲存或傳遞，不含中間的 tool 訊息。

        Returns:
            只包含 user 和 assistant 角色的訊息列表。
        """
        return [
            msg for msg in self.messages
            if msg["role"] in ("user", "assistant")
        ]