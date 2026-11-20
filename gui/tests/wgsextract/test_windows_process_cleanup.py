"""Issue #1162: close waits for descendants, but never ignores surviving processes."""

from __future__ import annotations

import shutil
import subprocess
import unittest
from pathlib import Path

GUI = Path(__file__).resolve().parents[2]


@unittest.skipUnless(shutil.which("pwsh"), "PowerShell is not installed")
class WindowsProcessCleanupTests(unittest.TestCase):
    def run_cleanup(
        self, lifetime: int, timeout: int
    ) -> subprocess.CompletedProcess[str]:
        helper = GUI / "scripts/windows-process-cleanup.ps1"
        source = """
$ErrorActionPreference = "Stop"
. $args[0]
$child = Start-Process (Get-Process -Id $PID).Path -ArgumentList @(
    "-NoProfile", "-Command", "Start-Sleep -Seconds $($args[1])"
) -PassThru
try {
    Wait-WindowsProcessCleanup -TimeoutSeconds $args[2] -GetProcesses {
        $child.Refresh()
        if (-not $child.HasExited) {
            [pscustomobject]@{
                ProcessId = $child.Id
                ParentProcessId = $PID
                Name = "cleanup-test-child"
                CommandLine = "Start-Sleep"
            }
        }
    }
    Write-Host "CLEANED"
}
finally {
    if (-not $child.HasExited) { $child.Kill() }
    $child.WaitForExit()
    $child.Dispose()
}
"""
        return subprocess.run(
            [
                str(shutil.which("pwsh")),
                "-NoProfile",
                "-Command",
                "& { " + source + " }",
                str(helper),
                str(lifetime),
                str(timeout),
            ],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )

    def test_1162_waits_for_delayed_descendant_exit(self) -> None:
        result = self.run_cleanup(lifetime=3, timeout=8)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("CLEANED", result.stdout)
        self.assertIn("Waiting for processes", result.stdout)

    def test_1162_reports_surviving_process_identity_at_deadline(self) -> None:
        result = self.run_cleanup(lifetime=30, timeout=1)
        self.assertNotEqual(result.returncode, 0)
        for fact in (
            "Processes remained",
            "pid=",
            "parent=",
            "cleanup-test-child",
            "Start-Sleep",
        ):
            self.assertIn(fact, result.stderr)
        self.assertNotIn("CLEANED", result.stdout)


if __name__ == "__main__":
    unittest.main()
