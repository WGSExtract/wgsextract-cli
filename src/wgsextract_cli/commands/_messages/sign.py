"""Messages for the sign commands."""

CLI_HELP = {
    "cmd_sign": "Write SHA-256 checksum manifests for output files, sign them with an OpenSSH key, and verify them later.",
    "cmd_sign-create": "Hash files or directories into a sha256sum-compatible SHA256SUMS manifest, signed with ssh-keygen when a key is given.",
    "cmd_sign-verify": "Check a SHA256SUMS manifest's OpenSSH signature against a trusted key, then re-hash every listed file.",
}
