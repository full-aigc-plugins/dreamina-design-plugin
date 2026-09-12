import json
import struct
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLUGIN_ID = "codex-dreamina-design"
DISPLAY_NAME = "Dreamina Design"
REPOSITORY = "https://github.com/partme-ai/codex-dreamina-design-plugin"
BRAND_COLOR = "#EC4899"

def load_json(relative: str) -> dict:
    target = ROOT / relative
    if not target.is_file():
        raise AssertionError(f"missing distribution file: {relative}")
    return json.loads(target.read_text(encoding="utf-8"))

def png_shape(relative: str) -> tuple[int, int, int]:
    data = (ROOT / relative).read_bytes()
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise AssertionError(f"not PNG: {relative}")
    width, height = struct.unpack(">II", data[16:24])
    return width, height, data[25]

class DistributionTests(unittest.TestCase):
    def test_validator_accepts_distribution(self) -> None:
        result = subprocess.run([sys.executable, str(ROOT / "scripts/validate_distribution.py"), str(ROOT)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_manifest_and_marketplace(self) -> None:
        manifest = load_json(".codex-plugin/plugin.json")
        self.assertEqual(manifest["name"], PLUGIN_ID)
        self.assertEqual(manifest["version"], "0.1.0")
        self.assertEqual(manifest["repository"], REPOSITORY)
        self.assertEqual(manifest["skills"], "./skills/")
        self.assertEqual(manifest["mcpServers"], "./.mcp.json")
        mcp = load_json(".mcp.json")["mcpServers"]["dreamina_design"]
        self.assertEqual(mcp["default_tools_approval_mode"], "prompt")
        self.assertEqual(mcp["tools"]["dreamina_submit_image"]["approval_mode"], "prompt")
        self.assertEqual(mcp["tools"]["dreamina_submit_video"]["approval_mode"], "prompt")
        interface = manifest["interface"]
        self.assertEqual(interface["displayName"], DISPLAY_NAME)
        self.assertEqual(interface["brandColor"], BRAND_COLOR)
        self.assertEqual(interface["logo"], "./assets/logo.png")
        self.assertEqual(interface["logoDark"], "./assets/logo-dark.png")
        self.assertEqual(interface["composerIcon"], "./assets/composer-icon.png")
        self.assertLessEqual(len(interface["defaultPrompt"]), 3)
        self.assertTrue(all(len(prompt) <= 128 for prompt in interface["defaultPrompt"]))
        marketplace = load_json(".agents/plugins/marketplace.json")
        entries = [entry for entry in marketplace["plugins"] if entry["name"] == PLUGIN_ID]
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["source"], {"source": "url", "url": REPOSITORY + ".git", "ref": "main"})
        self.assertEqual(entries[0]["policy"], {"installation": "AVAILABLE", "authentication": "ON_USE"})

    def test_structure_legal_and_brand_assets(self) -> None:
        for directory in ("assets", "skills", "schemas", "scripts", "tests"):
            self.assertTrue((ROOT / directory).is_dir(), directory)
        for filename in ("LICENSE", "NOTICE", "PRIVACY.md", "TERMS.md", "THIRD_PARTY_NOTICES.md", ".gitignore", "docs/portable-migration.md", "scripts/validate_distribution.py"):
            self.assertTrue((ROOT / filename).is_file(), filename)
        self.assertFalse((ROOT / "plugin.json").exists())
        self.assertFalse((ROOT / "mcp.json").exists())
        self.assertEqual(png_shape("assets/logo.png"), (1024, 1024, 6))
        self.assertEqual(png_shape("assets/logo-dark.png"), (1024, 1024, 6))
        self.assertEqual(png_shape("assets/composer-icon.png"), (256, 256, 6))

if __name__ == "__main__":
    unittest.main()
