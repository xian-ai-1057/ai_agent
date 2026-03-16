"""Skill Agent 設定檔。

集中管理 API 端點、模型參數、Skills 目錄等配置。
所有設定皆可透過環境變數覆蓋。
"""
import os
from dotenv import load_dotenv

load_dotenv()

# --- vLLM / OpenAI 相容 API ---
API_BASE_URL = os.getenv("API_BASE_URL")
API_KEY = os.getenv("SKILL_AGENT_API_KEY", "EMPTY")
# MODEL_NAME = os.getenv("SKILL_AGENT_MODEL", "llm-dgx-medium-01")
MODEL_NAME = os.getenv("SKILL_AGENT_MODEL", "llm-medium-01") # UAT

# --- 模型參數 ---
TEMPERATURE = float(os.getenv("SKILL_AGENT_TEMPERATURE", "0.7"))
TOP_P = float(os.getenv("SKILL_AGENT_TOP_P", "0.95"))
MAX_TOKENS = int(os.getenv("SKILL_AGENT_MAX_TOKENS", "4096"))

# --- Qwen3 思考模式 ---
ENABLE_THINKING = (
    os.getenv("SKILL_AGENT_ENABLE_THINKING", "true").lower()
)
ENABLE_THINKING = True

# --- Skills 目錄 ---
SKILLS_DIR = os.getenv("SKILL_AGENT_SKILLS_DIR", "./skills")

# --- Agent 行為 ---
# 單輪對話中允許的最大工具呼叫輪次（防止無限迴圈）
MAX_TOOL_ROUNDS = int(
    os.getenv("SKILL_AGENT_MAX_TOOL_ROUNDS", "6")
)

# --- Context Compaction ---
# 當 prompt_tokens 超過此閾值時自動觸發對話摘要壓縮。
# 建議設為模型 context window 的 70-80%。
# 例：Qwen3 32K → 24000，Qwen3 128K → 100000。
# 設為 0 表示停用 compaction。
COMPACTION_THRESHOLD = int(
    os.getenv("SKILL_AGENT_COMPACTION_THRESHOLD", "24000")
)

# --- 請求逾時（秒）---
REQUEST_TIMEOUT = int(
    os.getenv("SKILL_AGENT_REQUEST_TIMEOUT", "120")
)