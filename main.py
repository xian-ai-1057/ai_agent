"""Skill Agent CLI 入口。

提供互動式對話介面，支援多輪對話、
技能列表查看、對話重置等功能。
"""
import logging
import sys

import config
from agent import SkillAgent

logger = logging.getLogger(__name__)


def setup_logging(verbose: bool = False) -> None:
    """配置 logging。

    Args:
        verbose: 是否啟用 DEBUG 級別日誌。
    """
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    logging.getLogger("urllib3").setLevel(logging.WARNING)


def print_banner(agent: SkillAgent) -> None:
    """印出啟動資訊。"""
    skills = agent.manager.list_skills()
    print("\n" + "=" * 60)
    print("  Skill Agent")
    print(f"  模型: {config.MODEL_NAME}")
    print(f"  API:  {config.API_BASE_URL}")
    print(f"  思考模式: {'啟用' if config.ENABLE_THINKING else '關閉'}")
    print(f"  技能: {len(skills)} 個已載入")
    print("=" * 60)

    if skills:
        print("\n已載入的技能:")
        for s in skills:
            desc_short = s["description"][:60]
            if len(s["description"]) > 60:
                desc_short += "..."
            print(f"  • {s['name']}: {desc_short}")

    print(
        "\n指令: /skills 列出技能 | "
        "/reset 重置對話 | /quit 離開"
    )
    print("-" * 60)


def chat_loop() -> None:
    """互動式對話迴圈。"""
    agent = SkillAgent()
    print_banner(agent)

    while True:
        try:
            user_input = input("\n你: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n再見！")
            break

        if not user_input:
            continue

        # 處理指令
        if user_input.startswith("/"):
            cmd = user_input.lower()
            if cmd == "/quit":
                print("再見！")
                break
            elif cmd == "/reset":
                agent.reset()
                print("對話已重置。")
                continue
            elif cmd == "/skills":
                for s in agent.manager.list_skills():
                    print(f"\n【{s['name']}】")
                    print(f"  {s['description']}")
                continue
            elif cmd == "/history":
                history = agent.get_compact_history()
                if not history:
                    print("（尚無對話紀錄）")
                else:
                    for msg in history:
                        role = "你" if msg["role"] == "user" else "AI"
                        content = msg["content"][:80]
                        print(f"  [{role}] {content}")
                continue
            else:
                print(f"未知指令: {user_input}")
                continue

        # 呼叫 Agent
        print("\nAssistant: ", end="", flush=True)
        try:
            reply = agent.chat(user_input)
            print(reply)
        except Exception as e:
            logger.error("Agent 呼叫失敗: %s", e)
            print(f"\n發生錯誤: {e}")
            print("請檢查 vLLM 服務是否正常運作。")
            # 移除最後一則使用者訊息，避免錯誤狀態
            if agent.messages:
                agent.messages.pop()


def main() -> None:
    """程式入口點。"""
    verbose = "--verbose" in sys.argv or "-v" in sys.argv
    setup_logging(verbose)
    chat_loop()


if __name__ == "__main__":
    main()