"""Self-tests for scripts/vendor/skill_vendor.py.

Every test builds a throwaway upstream git repository and a consumer tree in a
temporary directory, then drives the vendor script as a subprocess exactly the
way CI does. The tamper cases are mutation checks: a gate that cannot detect
them proves nothing.
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "vendor" / "skill_vendor.py"

GIT_ENV = {
    **os.environ,
    "GIT_AUTHOR_NAME": "test",
    "GIT_AUTHOR_EMAIL": "test@example.com",
    "GIT_COMMITTER_NAME": "test",
    "GIT_COMMITTER_EMAIL": "test@example.com",
}


def run_git(args, cwd):
    subprocess.run(["git", *args], cwd=cwd, env=GIT_ENV, check=True,
                   capture_output=True, text=True)


def make_upstream(base: Path, skills: dict[str, str]) -> Path:
    """Create an upstream skill package repo with the given {name: body}."""
    upstream = base / "upstream"
    for name, body in skills.items():
        skill_dir = upstream / "skills" / name
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(
            f"---\nname: {name}\ndescription: {body}\n---\n\n# {name}\n"
        )
    run_git(["init", "--quiet"], upstream)
    run_git(["add", "-A"], upstream)
    run_git(["commit", "--quiet", "-m", "initial"], upstream)
    run_git(["tag", "-a", "v1.0.0", "-m", "release"], upstream)
    return upstream


def make_consumer(base: Path, upstream: Path, skills: list[str]) -> Path:
    consumer = base / "consumer"
    consumer.mkdir()
    (consumer / "skills").mkdir()
    lock = {
        "version": 1,
        "sources": [{
            "package": "demo-skills",
            "repo": str(upstream),
            "ref": "v1.0.0",
            "sha": "",
            "skills": skills,
            "dest": "skills/",
            "sha256": {},
        }],
    }
    (consumer / "skills.lock.json").write_text(json.dumps(lock, indent=2))
    return consumer


def vendor(command, consumer: Path, *extra):
    return subprocess.run(
        [sys.executable, str(SCRIPT), command, "--lock", "skills.lock.json", *extra],
        cwd=consumer, capture_output=True, text=True,
    )


class SkillVendorTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_update_then_check_roundtrip(self):
        upstream = make_upstream(self.base, {"demo-one": "first demo skill gate",
                                             "demo-two": "second demo skill gate"})
        consumer = make_consumer(self.base, upstream, ["demo-one", "demo-two"])
        result = vendor("update", consumer)
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        for name in ("demo-one", "demo-two"):
            self.assertTrue((consumer / "skills" / name / "SKILL.md").is_file())
        lock = json.loads((consumer / "skills.lock.json").read_text())
        source = lock["sources"][0]
        self.assertTrue(source["sha"])
        self.assertEqual({"demo-one", "demo-two"}, set(source["sha256"]))
        self.assertEqual(vendor("check", consumer, "--offline").returncode, 0)
        self.assertEqual(vendor("check", consumer).returncode, 0)

    def test_check_detects_in_tree_tampering(self):
        upstream = make_upstream(self.base, {"demo-one": "first demo skill gate"})
        consumer = make_consumer(self.base, upstream, ["demo-one"])
        self.assertEqual(vendor("update", consumer).returncode, 0)
        target = consumer / "skills" / "demo-one" / "SKILL.md"
        target.write_text(target.read_text() + "local edit\n")
        result = vendor("check", consumer, "--offline")
        self.assertEqual(result.returncode, 1)
        self.assertIn("differs from the lockfile digest", result.stdout)

    def test_check_detects_upstream_movement_and_update_resyncs(self):
        upstream = make_upstream(self.base, {"demo-one": "first demo skill gate"})
        consumer = make_consumer(self.base, upstream, ["demo-one"])
        self.assertEqual(vendor("update", consumer).returncode, 0)
        skill = upstream / "skills" / "demo-one" / "SKILL.md"
        skill.write_text(skill.read_text() + "upstream change\n")
        run_git(["add", "-A"], upstream)
        run_git(["commit", "--quiet", "-m", "change"], upstream)
        run_git(["tag", "-f", "-a", "v1.0.0", "-m", "release"], upstream)
        result = vendor("check", consumer)
        self.assertEqual(result.returncode, 1)
        self.assertIn("moved", result.stdout)
        self.assertEqual(vendor("update", consumer).returncode, 0)
        self.assertEqual(vendor("check", consumer).returncode, 0)
        self.assertIn("upstream change",
                      (consumer / "skills" / "demo-one" / "SKILL.md").read_text())

    def test_update_fails_when_skill_missing_upstream(self):
        upstream = make_upstream(self.base, {"demo-one": "first demo skill gate"})
        consumer = make_consumer(self.base, upstream, ["demo-one", "demo-ghost"])
        result = vendor("update", consumer)
        self.assertEqual(result.returncode, 1)
        self.assertIn("demo-ghost", result.stdout)

    def test_illegal_skill_name_is_rejected(self):
        upstream = make_upstream(self.base, {"demo-one": "first demo skill gate"})
        consumer = make_consumer(self.base, upstream, [".."])
        result = vendor("update", consumer)
        self.assertEqual(result.returncode, 1)
        self.assertIn("illegal skill name", result.stdout)

    def test_check_fails_when_managed_skill_deleted(self):
        upstream = make_upstream(self.base, {"demo-one": "first demo skill gate"})
        consumer = make_consumer(self.base, upstream, ["demo-one"])
        self.assertEqual(vendor("update", consumer).returncode, 0)
        import shutil
        shutil.rmtree(consumer / "skills" / "demo-one")
        result = vendor("check", consumer, "--offline")
        self.assertEqual(result.returncode, 1)
        self.assertIn("missing from the tree", result.stdout)


if __name__ == "__main__":
    unittest.main()
