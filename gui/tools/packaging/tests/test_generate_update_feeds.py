from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

GENERATOR = Path(__file__).resolve().parents[1] / "generate_update_feeds.py"
BASE_URL = "https://github.com/WGSExtract/wgsextract-cli/releases/download/v0.1.4"
RELEASE_FILES = {
    "tauri-windows": ("windows-x86_64", "WGS Extract_0.1.4_x64-setup.exe"),
    "tauri-linux": ("linux-x86_64", "WGSExtract_0.1.4_amd64.AppImage"),
    "tauri-macos-arm64": ("darwin-aarch64", "WGSExtract_aarch64.app.tar.gz"),
    "tauri-macos-x64": ("darwin-x86_64", "WGSExtract_x64.app.tar.gz"),
}


class GenerateUpdateFeedsTests(unittest.TestCase):
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.feed = self.root / "feed" / "latest.json"

    def write_artifact(self, relative_path: str, content: str = "archive\n") -> Path:
        path = self.root / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return path

    def write_release_artifacts(self, *omit: str) -> None:
        for target, (_, name) in RELEASE_FILES.items():
            if target not in omit:
                self.write_artifact(f"{target}/{name}")
                self.write_artifact(f"{target}/{name}.sig", f"{target}-signature\n")

    def generate(self, *options: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                sys.executable,
                str(GENERATOR),
                "--repo",
                "WGSExtract/wgsextract-cli",
                "--tag",
                "v0.1.4",
                "--version",
                "0.1.4",
                "--artifacts-root",
                str(self.root),
                "--output-dir",
                str(self.feed.parent),
                *options,
            ],
            capture_output=True,
            text=True,
        )

    def generate_release(self) -> subprocess.CompletedProcess[str]:
        return self.generate("--expected-targets", json.dumps(list(RELEASE_FILES)))

    def test_1237_complete_release_feed_links_all_signed_artifacts(self) -> None:
        self.write_release_artifacts()
        self.write_artifact("tauri-macos-arm64/WGSExtract.app/Contents/Info.plist")
        result = self.generate_release()
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(self.feed.read_text(encoding="utf-8"))
        self.assertEqual(payload["version"], "0.1.4")
        self.assertEqual(
            payload["platforms"],
            {
                platform: {
                    "signature": f"{target}-signature",
                    "url": f"{BASE_URL}/{name.replace(' ', '.')}",
                }
                for target, (platform, name) in RELEASE_FILES.items()
            },
        )

    def test_1237_release_validates_only_the_declared_targets(self) -> None:
        self.write_release_artifacts("tauri-macos-x64", "tauri-linux")
        result = self.generate("--expected-targets", '["tauri-macos-arm64"]')
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(self.feed.read_text(encoding="utf-8"))
        self.assertEqual(set(payload["platforms"]), {"darwin-aarch64"})

    def test_1237_missing_platform_cannot_replace_existing_feed(self) -> None:
        self.write_release_artifacts("tauri-macos-x64")
        self.write_artifact("tauri-macos-x64/WGSExtract_0.1.4_x64.dmg")
        self.feed.parent.mkdir()
        self.feed.write_text("previous feed", encoding="utf-8")
        result = self.generate_release()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("darwin-x86_64", result.stderr)
        self.assertIn("missing updater artifact", result.stderr)
        self.assertIn("*_x64.app.tar.gz", result.stderr)
        self.assertEqual(self.feed.read_text(encoding="utf-8"), "previous feed")

    def test_1237_missing_and_empty_signatures_fail_together(self) -> None:
        self.write_release_artifacts("tauri-windows", "tauri-linux")
        _, windows = RELEASE_FILES["tauri-windows"]
        _, linux = RELEASE_FILES["tauri-linux"]
        self.write_artifact(f"tauri-windows/{windows}")
        self.write_artifact(f"wrong-directory/{windows}.sig", "unrelated signature")
        self.write_artifact(f"tauri-linux/{linux}")
        self.write_artifact(f"tauri-linux/{linux}.sig", " \t\n")
        result = self.generate_release()
        self.assertNotEqual(result.returncode, 0)
        self.assertRegex(result.stderr, r"windows-x86_64: missing updater signature")
        self.assertRegex(result.stderr, r"linux-x86_64: empty updater signature")
        self.assertIn(f"{windows}.sig", result.stderr)
        self.assertIn(f"{linux}.sig", result.stderr)
        self.assertFalse(self.feed.exists())

    def test_1237_refuses_two_matching_artifacts_even_if_only_one_is_signed(
        self,
    ) -> None:
        self.write_release_artifacts()
        alternate = self.write_artifact("tauri-linux/WGSExtract_other.AppImage")
        result = self.generate_release()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("linux-x86_64: ambiguous", result.stderr)
        self.assertIn(RELEASE_FILES["tauri-linux"][1], result.stderr)
        self.assertIn(alternate.name, result.stderr)
        self.assertFalse(self.feed.exists())

    def test_1237_refuses_artifacts_that_would_overwrite_the_same_release_asset(
        self,
    ) -> None:
        self.write_release_artifacts()
        name = RELEASE_FILES["tauri-windows"][1]
        for conflicting_name in (name, name.replace(" ", ".")):
            with self.subTest(conflicting_name=conflicting_name):
                conflicting = self.write_artifact(f"other-directory/{conflicting_name}")
                result = self.generate_release()
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("ambiguous", result.stderr)
                self.assertIn(str(conflicting.relative_to(self.root)), result.stderr)
                self.assertFalse(self.feed.exists())
                conflicting.unlink()

    def test_1237_unsigned_development_build_requires_explicit_opt_in(self) -> None:
        linux = self.write_artifact("tauri-linux/WGSExtract_0.1.4_amd64.AppImage")
        self.write_artifact("tauri-windows/WGSExtract_0.1.4_x64-setup.exe")

        self.assertNotEqual(self.generate().returncode, 0)
        result = self.generate("--allow-unsigned")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("No updater feed", result.stderr)
        self.assertFalse(self.feed.exists())

        signature = linux.with_suffix(".AppImage.sig")
        signature.write_text("linux-signature", encoding="utf-8")
        result = self.generate("--allow-unsigned")
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(self.feed.read_text(encoding="utf-8"))
        self.assertEqual(set(payload["platforms"]), {"linux-x86_64"})

        signature.unlink()
        result = self.generate("--allow-unsigned")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("stale feed", result.stderr)
        self.assertIn("latest.json", result.stderr)
        self.assertEqual(json.loads(self.feed.read_text(encoding="utf-8")), payload)

    def test_1237_empty_or_unknown_expectations_cannot_bypass_validation(self) -> None:
        self.write_release_artifacts()
        for targets in ("", "[]", '["tauri-typo"]'):
            with self.subTest(targets=targets):
                result = self.generate("--expected-targets", targets)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("--expected-targets", result.stderr)
                self.assertFalse(self.feed.exists())


if __name__ == "__main__":
    unittest.main()
