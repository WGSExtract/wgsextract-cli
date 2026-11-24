## Changed

- Reference library bootstrap `v0.4.0` (`wgsextract ref bootstrap`) expands CombinedKit v3 into CombinedKit v5 with merged **23andMe v4** (`v4a–c`), merged **FTDNA v2** (`v2a–d`), and the consumer vendor formats from [#1107 (Microarray: letter-named vendor variants and CombinedKit v5)](https://github.com/WGSExtract/wgsextract-cli/pull/1107), plus the rebuilt CHM13 v2 site map (`2,992,588` sites) and chip-revision catalogue. CombinedKit v3 stays frozen via classic `v0.2.0` templates and frozen `23andMe_V4_v3` / `FTDNA_V2_v3` deltas. Rerunning `wgsextract ref bootstrap` cleanly upgrades an existing `v0.2.0` or `v0.3.0` library.
- `ancestry_v2` now aliases to `Ancestry_V2ab` (merged `v2a+v2b`), and unverified/minor date- and size-qualified variants (`23andme_v5_2025`, `ancestry_v2_2021`, `ancestry_v2_2024`, `myheritage_gsa_2025`, `myheritage_gsa_2025_609424`) have been removed.

## New

- `wgsextract microarray --formats combined_v3` writes `<sample>_CombinedKit_v3.txt` (the frozen classic 14-template CombinedKit v3 subset) alongside CombinedKit v5 (`--formats all` / `combined_v5`), so users can generate either or both combined kits.

## Desktop app

- The Microarray page keeps **CombinedKit v3** (`v3`) alongside **CombinedKit v5** (`v5`, expanding v3) under **WGSE → Combined**, places `23andMe v1/v2` before `v3` on the 23andMe row, places `FTDNA v1 (Affy)` before `v2` on the FamilyTreeDNA row, puts FTDNA's GRCh38 Y files (`v2y`, `v3y`, `v4y`) on a dedicated **FTDNA (Y only, Build 38)** row, exposes the CombinedKit v5 consumer formats (`ancestry_v2ab`, `ancestry_v2cd`, `ancestry_v2e`, `ftdna_v4`, `myheritage_v3`, `tellmegen`, `genera`, `mthfr`, `meudna_v1`, `meudna_v2`, `selfdecode`, `nucleus`), and removes the dropped minor date/size variants and standalone `ancestry_v2`.
