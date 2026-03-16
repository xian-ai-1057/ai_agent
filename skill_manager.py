"""Skill 管理模組。

負責掃描 skills 目錄、解析 SKILL.md frontmatter、
產生 registry prompt、以及按需讀取 skill 內容。
"""
import logging
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)


class SkillManager:
    """管理所有 skill 的索引與檔案讀取。

    掃描指定目錄下的 skill 資料夾，解析每個 SKILL.md
    的 YAML frontmatter 建立索引，並提供按需讀取介面。

    Attributes:
        skills_dir: skills 根目錄路徑。
        registry: skill metadata 索引，key 為 skill name。
    """

    def __init__(self, skills_dir: str) -> None:
        """初始化並掃描 skills 目錄。

        Args:
            skills_dir: skills 根目錄路徑。
        """
        self.skills_dir = Path(skills_dir)
        self.registry: dict[str, dict[str, Any]] = {}
        self._scan_skills()

    def _scan_skills(self) -> None:
        """掃描目錄，解析所有 SKILL.md 的 frontmatter。"""
        if not self.skills_dir.exists():
            logger.warning("Skills 目錄不存在: %s", self.skills_dir)
            return

        for skill_path in sorted(self.skills_dir.iterdir()):
            if not skill_path.is_dir():
                continue
            skill_md = skill_path / "SKILL.md"
            if not skill_md.exists():
                logger.debug("跳過無 SKILL.md 的目錄: %s", skill_path)
                continue

            meta = self._parse_frontmatter(skill_md)
            if meta and "name" in meta:
                meta["_path"] = str(skill_path)
                meta["_skill_md"] = str(skill_md)
                self.registry[meta["name"]] = meta
                logger.info(
                    "載入 skill: %s (%s)",
                    meta["name"], skill_path.name
                )
            else:
                logger.warning(
                    "無效的 SKILL.md (缺少 name): %s", skill_md
                )

        logger.info("共載入 %d 個 skills", len(self.registry))

    def _parse_frontmatter(self, path: Path) -> dict[str, Any] | None:
        """解析 SKILL.md 的 YAML frontmatter。

        Args:
            path: SKILL.md 檔案路徑。

        Returns:
            解析後的 metadata 字典，解析失敗回傳 None。
        """
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as e:
            logger.error("無法讀取 %s: %s", path, e)
            return None

        # 提取 --- ... --- 之間的 YAML
        if not text.startswith("---"):
            return None
        end_idx = text.find("---", 3)
        if end_idx == -1:
            return None

        yaml_str = text[3:end_idx].strip()
        try:
            return yaml.safe_load(yaml_str)
        except yaml.YAMLError as e:
            logger.error("YAML 解析失敗 %s: %s", path, e)
            return None

    def build_registry_prompt(self) -> str:
        """產生包含所有 skill metadata 的 prompt 片段。

        Returns:
            XML 格式的 available_skills 字串，用於注入
            system prompt。
        """
        if not self.registry:
            return ""

        entries = []
        for name, meta in self.registry.items():
            desc = meta.get("description", "（無描述）")
            # 清理多行 description
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

    def read_skill(self, skill_name: str) -> str:
        """讀取指定 skill 的 SKILL.md 完整內容。

        Args:
            skill_name: skill 名稱（對應 frontmatter 中的 name）。

        Returns:
            SKILL.md 的完整文字內容。
            找不到時回傳錯誤訊息。
        """
        meta = self.registry.get(skill_name)
        if meta is None:
            available = ", ".join(self.registry.keys())
            return (
                f"找不到名為 '{skill_name}' 的 skill。"
                f"可用的 skills: {available}"
            )

        path = Path(meta["_skill_md"])
        try:
            return path.read_text(encoding="utf-8")
        except OSError as e:
            return f"讀取 skill 失敗: {e}"

    def read_reference(
        self, skill_name: str, filename: str
    ) -> str:
        """讀取指定 skill 的 references 子檔案。

        Args:
            skill_name: skill 名稱。
            filename: references 目錄下的檔案名稱。

        Returns:
            參考檔案的文字內容。
            找不到時回傳錯誤訊息。
        """
        meta = self.registry.get(skill_name)
        if meta is None:
            return f"找不到名為 '{skill_name}' 的 skill。"

        skill_path = Path(meta["_path"])
        ref_path = skill_path / "references" / filename

        # 安全檢查：防止路徑穿越
        try:
            ref_path.resolve().relative_to(skill_path.resolve())
        except ValueError:
            return f"非法路徑: {filename}"

        if not ref_path.exists():
            # 列出可用的 reference 檔案
            refs_dir = skill_path / "references"
            if refs_dir.exists():
                available = [
                    f.name for f in refs_dir.iterdir()
                    if f.is_file()
                ]
                return (
                    f"找不到 '{filename}'。"
                    f"可用的參考檔案: {', '.join(available)}"
                )
            return f"此 skill 沒有 references 目錄。"

        try:
            return ref_path.read_text(encoding="utf-8")
        except OSError as e:
            return f"讀取參考檔案失敗: {e}"

    def list_skills(self) -> list[dict[str, str]]:
        """列出所有已載入的 skill 摘要資訊。

        Returns:
            包含 name 和 description 的字典列表。
        """
        return [
            {
                "name": name,
                "description": " ".join(
                    meta.get("description", "").split()
                ),
            }
            for name, meta in self.registry.items()
        ]