"""Observe distribution bytes, independently of host discovery/session adoption.

Temporary complete skill copies exercise the README's manual comparison boundary.
No installer, host settings, global copies, carrier or network service is invoked.
"""

import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
SKILL = ROOT / "skills" / "sshx"


def directory_contents(root: Path) -> dict[str, str]:
    """Observe published file bytes; build outputs and Python caches are not shipped."""
    contents: dict[str, str] = {}
    for directory, subdirs, files in os.walk(root):
        subdirs[:] = [name for name in subdirs if name not in {".lake", "__pycache__"}]
        for name in files:
            path = Path(directory) / name
            contents[str(path.relative_to(root))] = hashlib.sha256(path.read_bytes()).hexdigest()
    return contents



class SkillDistributionTests(unittest.TestCase):
    def test_complete_copy_symlink_edit_and_manual_refresh(self) -> None:
        expected = directory_contents(SKILL)
        self.assertIn("SKILL.md", expected)
        self.assertIn("scripts/run-codex-worker.sh", expected)
        self.assertIn("CODEX_WORKER_SPEC.md", expected)
        manifest_version = json.loads((ROOT / "package.json").read_text())["version"]
        revision = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
        self.assertEqual(len(revision), 40)
        with tempfile.TemporaryDirectory(prefix="sshx-distribution-") as temp:
            copied = Path(temp) / "copied"
            shutil.copytree(SKILL, copied, ignore=shutil.ignore_patterns(".lake", "__pycache__"))
            selected = Path(temp) / "selected"
            selected.symlink_to(copied, target_is_directory=True)
            self.assertEqual(selected.resolve(), copied.resolve())
            self.assertEqual(directory_contents(selected), expected)
            observed = subprocess.check_output(
                ["shasum", "-a", "256", str(selected / "SKILL.md")], text=True
            ).split()[0]
            self.assertEqual(observed, expected["SKILL.md"])

            # Manifest/checkout metadata stays identical while copied content diverges.
            with (copied / "SKILL.md").open("a") as stream:
                stream.write("\nLocal distribution edit.\n")
            self.assertNotEqual(directory_contents(selected)["SKILL.md"], observed)
            self.assertEqual(json.loads((ROOT / "package.json").read_text())["version"], manifest_version)
            self.assertEqual(subprocess.check_output(
                ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
            ).strip(), revision)

            # Refresh the whole unit in this fixture, including a divergent support file.
            support = copied / "CODEX_WORKER_SPEC.md"
            support.write_text("stale support file\n")
            shutil.copytree(SKILL, copied, dirs_exist_ok=True,
                            ignore=shutil.ignore_patterns(".lake", "__pycache__"))
            self.assertEqual(directory_contents(selected), expected)
            support.write_text("locally changed support file\n")
            changed = directory_contents(selected)
            self.assertEqual(changed["SKILL.md"], observed)
            self.assertNotEqual(changed, expected)  # SKILL.md hash alone misses support drift.

            # Repointing a discovery link selects different bytes; no session reload is inferred.
            alternate = Path(temp) / "alternate"
            shutil.copytree(SKILL, alternate, ignore=shutil.ignore_patterns(".lake", "__pycache__"))
            selected.unlink()
            selected.symlink_to(alternate, target_is_directory=True)
            self.assertEqual(selected.resolve(), alternate.resolve())
            self.assertEqual(directory_contents(selected), expected)


if __name__ == "__main__":
    unittest.main()
