"""Protect cross-host public identity from obsolete Codex product prefixes."""

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
BANNED_PUBLIC_NAMES = ("codex" + "-dreamina-design", "codex" + " Dreamina Design", "codex" + "-Dreamina-Design", "codex" + "-dreamina-3d", "codex" + "-blender", "codex" + "-maya",)
TEXT_SUFFIXES = {".md", ".json", ".py", ".mjs", ".js", ".toml", ".yaml", ".yml", ".svg"}
EXCLUDED_PARTS = {".git", ".mimosa", ".worktrees", ".superpowers", "artifacts", "openspec", "superpowers", "verification", "__pycache__"}


class CrossHostNamingTest(unittest.TestCase):
    def test_public_files_and_content_use_host_neutral_names(self) -> None:
        violations: list[str] = []
        for path in ROOT.rglob("*"):
            if path == Path(__file__) or any(part in EXCLUDED_PARTS for part in path.parts):
                continue
            relative = path.relative_to(ROOT).as_posix()
            for obsolete in BANNED_PUBLIC_NAMES:
                if obsolete.lower() in relative.lower():
                    violations.append(f"path:{relative}:{obsolete}")
            if not path.is_file() or path.suffix.lower() not in TEXT_SUFFIXES:
                continue
            content = path.read_text(encoding="utf-8", errors="ignore")
            for obsolete in BANNED_PUBLIC_NAMES:
                if obsolete in content:
                    violations.append(f"content:{relative}:{obsolete}")
        self.assertEqual([], violations)


if __name__ == "__main__":
    unittest.main()
