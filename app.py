"""Skill Agent Streamlit 測試界面。

啟動方式：
    streamlit run app.py

支援功能：
    - 多輪對話（歷史保存在 session_state）
    - 側邊欄即時調整模型參數
    - 展示 Qwen3 思考過程（可摺疊）
    - 展示工具呼叫追蹤
    - 顯示已載入的 Skills 清單
    - System Prompt 預覽
"""
import json
import sys
from datetime import datetime
from pathlib import Path

import streamlit as st

# 確保 skill_agent 可被 import
sys.path.insert(0, str(Path(__file__).parent))

import config
from agent import SkillAgent


# ============================================================
# 頁面配置
# ============================================================

st.set_page_config(
    page_title="Skill Agent",
    page_icon="🤖",
    layout="wide",
)


# ============================================================
# 輔助函式
# ============================================================

def _format_chat_history(history: list[dict]) -> str:
    """將對話歷史格式化為可讀的純文字。

    包含所有訊息內容及追蹤資訊（思考過程、工具呼叫、
    工具結果），方便後續分析。

    Args:
        history: st.session_state["chat_history"]。

    Returns:
        格式化後的純文字字串。
    """
    lines: list[str] = []
    lines.append(f"Skill Agent 對話記錄")
    lines.append(
        f"匯出時間: {datetime.now():%Y-%m-%d %H:%M:%S}"
    )
    lines.append(
        f"模型: {config.MODEL_NAME}  "
        f"API: {config.API_BASE_URL}"
    )
    lines.append("=" * 60)

    for idx, entry in enumerate(history, 1):
        role_label = (
            "使用者" if entry["role"] == "user"
            else "Assistant"
        )
        lines.append("")
        lines.append(f"[{idx}] {role_label}:")
        lines.append(entry["content"])

        # 輸出追蹤資訊
        trace = entry.get("trace", [])
        if trace:
            lines.append("")
            lines.append(f"  --- 追蹤紀錄（{len(trace)} 步）---")
            for step in trace:
                if step["type"] == "thinking":
                    lines.append(
                        f"  [思考 R{step['round']}]"
                    )
                    lines.append(f"  {step['content']}")
                elif step["type"] == "tool_call":
                    args_str = json.dumps(
                        step["arguments"],
                        ensure_ascii=False,
                    )
                    lines.append(
                        f"  [工具呼叫 R{step['round']}] "
                        f"{step['name']}({args_str})"
                    )
                elif step["type"] == "tool_result":
                    lines.append(
                        f"  [工具結果] {step['name']}: "
                        f"{step['content']}"
                    )
            lines.append("  --- 追蹤結束 ---")

        lines.append("-" * 60)

    return "\n".join(lines)


# ============================================================
# 側邊欄：設定
# ============================================================

with st.sidebar:
    st.title("⚙️ 設定")

    st.subheader("API 連線")
    api_base = st.text_input(
        "API Base URL", value=config.API_BASE_URL
    )
    model_name = st.text_input(
        "Model Name", value=config.MODEL_NAME
    )

    st.subheader("模型參數")
    temperature = st.slider(
        "Temperature", 0.0, 2.0, config.TEMPERATURE, 0.05
    )
    top_p = st.slider(
        "Top-P", 0.0, 1.0, config.TOP_P, 0.05
    )
    max_tokens = st.number_input(
        "Max Tokens", 256, 16384, config.MAX_TOKENS, 256
    )
    enable_thinking = st.toggle(
        "Qwen3 思考模式", value=config.ENABLE_THINKING
    )

    st.subheader("Skills")
    skills_dir = st.text_input(
        "Skills 目錄", value=config.SKILLS_DIR
    )

    st.divider()

    # 套用設定按鈕
    if st.button("🔄 套用設定並重新初始化", use_container_width=True):
        config.API_BASE_URL = api_base
        config.MODEL_NAME = model_name
        config.TEMPERATURE = temperature
        config.TOP_P = top_p
        config.MAX_TOKENS = max_tokens
        config.ENABLE_THINKING = enable_thinking
        config.SKILLS_DIR = skills_dir
        # 清除 agent 快取，強制重建
        if "agent" in st.session_state:
            del st.session_state["agent"]
        if "chat_history" in st.session_state:
            del st.session_state["chat_history"]
        st.rerun()

    # 即時同步滑桿值（不需重建 agent）
    config.TEMPERATURE = temperature
    config.TOP_P = top_p
    config.MAX_TOKENS = max_tokens
    config.ENABLE_THINKING = enable_thinking

    st.divider()

    if st.button("🗑️ 清除對話", use_container_width=True):
        if "agent" in st.session_state:
            st.session_state["agent"].reset()
        st.session_state["chat_history"] = []
        st.rerun()

    # 下載對話記錄
    if st.session_state.get("chat_history"):
        st.download_button(
            label="📥 下載對話記錄",
            data=_format_chat_history(
                st.session_state["chat_history"]
            ),
            file_name=(
                f"chat_{datetime.now():%Y%m%d_%H%M%S}.txt"
            ),
            mime="text/plain",
            use_container_width=True,
        )


# ============================================================
# 初始化 Agent
# ============================================================

def get_agent() -> SkillAgent:
    """取得或建立 SkillAgent（快取在 session_state）。"""
    if "agent" not in st.session_state:
        try:
            st.session_state["agent"] = SkillAgent()
        except Exception as e:
            st.error(f"Agent 初始化失敗: {e}")
            st.stop()
    return st.session_state["agent"]


agent = get_agent()

# 初始化對話歷史
if "chat_history" not in st.session_state:
    st.session_state["chat_history"] = []


# ============================================================
# 主畫面：對話介面
# ============================================================

st.title("🤖 Skill Agent")
st.caption(
    f"模型: `{config.MODEL_NAME}` · "
    f"API: `{config.API_BASE_URL}` · "
)


def _show_trace_steps(trace: list[dict]) -> None:
    """渲染追蹤步驟內容（不含外層 expander）。"""
    for step in trace:
        if step["type"] == "thinking":
            st.markdown(
                f"**💭 思考（第 {step['round']} 輪）**"
            )
            st.info(step["content"])

        elif step["type"] == "tool_call":
            args_str = json.dumps(
                step["arguments"],
                ensure_ascii=False,
                indent=2,
            )
            st.markdown(
                f"**🔧 呼叫工具（第 {step['round']} 輪）**"
                f"：`{step['name']}`"
            )
            st.code(args_str, language="json")

        elif step["type"] == "tool_result":
            st.markdown(
                f"**📋 工具結果**：`{step['name']}`"
            )
            content = step["content"]
            if len(content) > 1000:
                st.code(
                    content[:1000] + "\n... (已截斷)",
                    language="markdown",
                )
            else:
                st.code(content, language="markdown")


# 渲染歷史訊息
for entry in st.session_state["chat_history"]:
    with st.chat_message(entry["role"]):
        st.markdown(entry["content"])

        # 如果有追蹤資訊，顯示在摺疊區
        if entry.get("trace"):
            with st.expander(
                f"🔍 追蹤紀錄（{len(entry['trace'])} 步）",
                expanded=False,
            ):
                _show_trace_steps(entry["trace"])


# 處理使用者輸入
user_input = st.chat_input("輸入訊息...")

if user_input:
    # 顯示使用者訊息
    st.session_state["chat_history"].append({
        "role": "user",
        "content": user_input,
    })
    with st.chat_message("user"):
        st.markdown(user_input)

    # 呼叫 Agent
    with st.chat_message("assistant"):
        with st.spinner("思考中..."):
            try:
                result = agent.chat_with_trace(user_input)
                reply = result["content"]
                trace = result["trace"]
            except Exception as e:
                reply = f"⚠️ 發生錯誤: {e}"
                trace = []

        st.markdown(reply)

        # 顯示追蹤
        if trace:
            with st.expander(
                f"🔍 追蹤紀錄（{len(trace)} 步）",
                expanded=True,
            ):
                _show_trace_steps(trace)

    # 存入歷史
    st.session_state["chat_history"].append({
        "role": "assistant",
        "content": reply,
        "trace": trace,
    })
    st.rerun()