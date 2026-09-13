import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SKILL_ROOT = REPO_ROOT / "skills" / "dreamina-cli"
OFFICIAL_WORKFLOW = SKILL_ROOT / "references" / "official-cli-install-to-use.md"

OFFICIAL_COMMANDS = (
    "curl -fsSL https://jimeng.jianying.com/cli | bash",
    "dreamina -h",
    "dreamina login",
    "dreamina login --headless",
    "dreamina login checklogin --device_code=<device_code> --poll=30",
    "dreamina relogin",
    "dreamina logout",
    "dreamina user_credit",
    "dreamina text2image",
    "dreamina image2image",
    "dreamina text2video",
    "dreamina image2video",
    "dreamina frames2video",
    "dreamina multiframe2video",
    "dreamina multimodal2video",
    "dreamina image_upscale",
    "dreamina query_result --submit_id=<submit_id>",
    "dreamina query_result --submit_id=<submit_id> --download_dir=./downloads",
    "dreamina list_task --gen_status=success",
    'dreamina session create "<project_name>"',
    "dreamina session list",
    'dreamina session search "<keyword>"',
    'dreamina session rename <session_id> "<new_name>"',
    "dreamina session delete <session_id>",
    "dreamina version",
)


class OfficialCliSkillCoverageTests(unittest.TestCase):
    def test_umbrella_skill_links_the_official_install_to_use_workflow(self) -> None:
        skill = (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("references/official-cli-install-to-use.md", skill)

    def test_packaged_workflow_covers_every_official_command(self) -> None:
        workflow = OFFICIAL_WORKFLOW.read_text(encoding="utf-8")
        for command in OFFICIAL_COMMANDS:
            with self.subTest(command=command):
                self.assertIn(command, workflow)

    def test_packaged_workflow_covers_operational_recovery(self) -> None:
        workflow = OFFICIAL_WORKFLOW.read_text(encoding="utf-8")
        for required in (
            "~/.dreamina_cli/logs/",
            "AigcComplianceConfirmationRequired",
            "submit_id",
            "积分",
            "明确授权",
            "优先更新 CLI",
        ):
            with self.subTest(required=required):
                self.assertIn(required, workflow)


if __name__ == "__main__":
    unittest.main()
