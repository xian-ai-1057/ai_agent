"""Skill MCP Server (FastMCP 版本)。

使用 mcp.server.fastmcp.FastMCP 高階 API，將 SkillManager
的能力 (list_skills, read_skill, read_skill_reference)
對外暴露為 MCP tools。

啟動方式:
    python mcp_server.py [--skills-dir PATH]

或透過環境變數:
    SKILL_AGENT_SKILLS_DIR=./skills python mcp_server.py

依賴:
    pip install mcp

協定參考:
- https://modelcontextprotocol.io/
- https://github.com/modelcontextprotocol/python-sdk

備註:
- FastMCP 預設以 stdio 為 transport，與 MCPStdioClient 直接相容
- 所有 log 走 stderr，避免污染 stdout 的 JSON-RPC 通道
- tool 的 schema 由 type hints + docstring 自動產生
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys

from mcp.server.fastmcp import FastMCP

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
# 解析 skills_dir：CLI > 環境變數 > config 預設
# ============================================================
def _resolve_skills_dir(argv: list[str] | None = None) -> str:
    parser = argparse.ArgumentParser(
        description="Skill MCP Server (FastMCP)",
        add_help=True,
    )
    parser.add_argument(
        "--skills-dir",
        default=None,
        help="skills 根目錄 (覆寫環境變數與 config 預設)",
    )
    # 不擋未知參數，讓 FastMCP 將來若加 CLI 參數也能傳遞
    args, _ = parser.parse_known_args(argv)
    if args.skills_dir:
        return args.skills_dir
    return os.getenv("SKILL_AGENT_SKILLS_DIR", config.SKILLS_DIR)


SKILLS_DIR = _resolve_skills_dir()
manager = SkillManager(SKILLS_DIR)
logger.info(
    "SkillManager 載入完成: dir=%s, skills=%d",
    SKILLS_DIR,
    len(manager.registry),
)


# ============================================================
# 建立 FastMCP server
# ============================================================
mcp = FastMCP(
    name="skill-mcp-server",
    instructions=(
        "提供 skill registry 與 SKILL.md 內容存取。"
        "client 端可先呼叫 list_skills 取得索引，"
        "再依需求呼叫 read_skill / read_skill_reference 載入內容。"
    ),
)


@mcp.tool()
def list_skills() -> str:
    """列出所有已註冊的 skills（含 name 與 description）。

    回傳值為 JSON 字串，格式為
    [{"name": str, "description": str}, ...]。
    用於 client 端建立 skill registry 索引。
    """
    skills = manager.list_skills()
    return json.dumps(skills, ensure_ascii=False)


@mcp.tool()
def read_skill(skill_name: str) -> str:
    """讀取指定 skill 的完整指引（SKILL.md）。

    當使用者的問題與某個 skill 的描述相符時，
    呼叫此工具來載入該 skill 的完整指引，
    然後嚴格按照指引中的流程和格式來回覆使用者。

    Args:
        skill_name: skill 名稱，必須是 available_skills 中
            列出的名稱之一。
    """
    return manager.read_skill(skill_name)


@mcp.tool()
def read_skill_reference(skill_name: str, filename: str) -> str:
    """讀取 skill 的補充參考資料。

    當 SKILL.md 中提到需要參考 references/ 下的特定檔案時，
    使用此工具載入。不要主動猜測檔案名稱，
    請依據 SKILL.md 中的明確指示來呼叫。

    Args:
        skill_name: skill 名稱。
        filename: 參考檔案名稱，例如 antipatterns.md。
    """
    return manager.read_reference(skill_name, filename)


def main() -> None:
    """以 stdio transport 啟動 MCP server。"""
    logger.info("啟動 FastMCP server (stdio transport)")
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
