"""Messages for the sample commands."""

CLI_HELP = {
    "cmd_sample": "Show or correct what is known about a sample (sex, sequencing platform, build, depth), recorded once in its folder's wgsextract.sample.toml.",
    "cmd_sample-show": "Show the sample's overrides, the facts commands detected from each of its files (with method, confidence and date) and its recorded results.",
    "cmd_sample-set": "Set an override (--sex, --platform) that commands use instead of what they detect: a sex for the whole genome folder, else for --input alone. Commands warn when the reads disagree.",
    "cmd_sample-unset": "Remove an override so commands detect that fact again: the folder-wide one and, with --input, that file's own.",
}
