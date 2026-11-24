"""Help entries for ``report-issue``."""

from . import CommandHelp, _h

ENTRIES: dict[str, CommandHelp] = {
    "report-issue": _h(
        "Prints a redacted bug report and its GitHub link and saves the "
        "report as issue-report-<time>.md in state/reports (setting "
        "state_directory); opens the browser only after you agree or with --open.",
        "wgsextract report-issue",
        "wgsextract report-issue --last-failed --print",
        "wgsextract report-issue --run out/sample.vcf.gz.manifest.json --open",
        "wgsextract report-issue --title 'vcf snp hangs' --description 'Stops at 40%.'",
    ),
}
