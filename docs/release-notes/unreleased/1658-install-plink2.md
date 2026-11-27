## New

- PLINK 2, used by `score pgs --plink2-check`, now installs with
  `wgsextract deps install plink2` (the Tools page's Install button) instead
  of by hand: from Bioconda into its own Pixi environment on Linux and macOS,
  and on Windows x64 from the author's official build, pinned by version and
  SHA-256. `deps uninstall plink2` removes it. Dorado stays user-installed,
  because its licence allows only research use with Nanopore data; the Tools
  note now says so
  ([#1658, install PLINK 2](https://github.com/WGSExtract/wgsextract-cli/pull/1658)).
