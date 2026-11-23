## Changed

- Reference library bootstrap `v0.3.0` (`wgsextract ref bootstrap`) expands CombinedKit v3 into CombinedKit v5 by including all 21 CLI-packaged consumer microarray templates (the 14 formats added in [#1107 (Microarray: letter-named vendor variants and CombinedKit v5)](https://github.com/WGSExtract/wgsextract-cli/pull/1107) plus the 7 earlier packaged templates and `corrections.tsv`) while keeping every CombinedKit v3 file intact and retaining the packaged CLI fallback. Rerunning `wgsextract ref bootstrap` upgrades an existing `v0.2.0` library without overwriting existing files.

## Desktop app

- The Microarray page now labels the combined output as **CombinedKit v5** (expanding CombinedKit v3) and exposes the CombinedKit v5 consumer formats (`ancestry_v2ab`, `ancestry_v2cd`, `ancestry_v2e`, `ftdna_v4`, `ftdna_v2y`, `ftdna_v3y`, `ftdna_v4y`, `myheritage_v3`, `tellmegen`, `genera`, `mthfr`, `meudna_v1`, `meudna_v2`, `selfdecode`, `nucleus`, plus advanced legacy variants `23andme_v2`, `23andme_v5_2025`, and `ftdna_v1_affy`).
