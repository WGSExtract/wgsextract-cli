## Changed

- Reference library bootstrap `v0.4.0` (`wgsextract ref bootstrap`) expands CombinedKit v3 into CombinedKit v5 with merged **23andMe v4** (`v4a–c`), merged **FTDNA v2** (`v2a–d`), and the consumer vendor formats from [#1107 (Microarray: letter-named vendor variants and CombinedKit v5)](https://github.com/WGSExtract/wgsextract-cli/pull/1107), plus the rebuilt CHM13 v2 site map (`2,992,588` sites) and chip-revision catalogue. CombinedKit v3 stays frozen via classic `v0.2.0` templates and frozen `23andMe_V4_v3` / `FTDNA_V2_v3` deltas. Rerunning `wgsextract ref bootstrap` cleanly upgrades an existing `v0.2.0` or `v0.3.0` library.
- `ancestry_v2` now aliases to `Ancestry_V2ab` (merged `v2a+v2b`), and unverified/minor date- and size-qualified variants (`23andme_v5_2025`, `ancestry_v2_2021`, `ancestry_v2_2024`, `myheritage_gsa_2025`, `myheritage_gsa_2025_609424`) have been removed.

## New

- `wgsextract microarray --formats combined_v3` writes `<sample>_CombinedKit_v3.txt` (the frozen classic 14-template CombinedKit v3 subset) alongside CombinedKit v5 (`--formats all` / `combined_v5`), so users can generate either or both combined kits.
