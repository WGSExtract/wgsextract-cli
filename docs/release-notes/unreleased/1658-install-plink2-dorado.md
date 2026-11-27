## New

- PLINK 2, used by `score pgs --plink2-check`, now installs with
  `wgsextract deps install plink2` (the Tools page's Install button) instead
  of by hand: from Bioconda into its own Pixi environment on Linux and macOS,
  and on Windows x64 from the author's official build, pinned by version and
  SHA-256. `deps uninstall plink2` removes it
  ([#1658, install PLINK 2 and Dorado](https://github.com/WGSExtract/wgsextract-cli/pull/1658)).
- Dorado 2.2.0, used by `basecall`, now installs with
  `wgsextract deps install dorado --accept-license` on Linux x64 and ARM64,
  Apple silicon Macs and Windows x64: ONT's official release, pinned by
  SHA-256. Dorado is licensed under the Oxford Nanopore Technologies PLC.
  Public License Version 2.0, for research use only; install shows the licence
  and downloads nothing until you accept it. `deps uninstall dorado` removes it
  ([#1658](https://github.com/WGSExtract/wgsextract-cli/pull/1658)).
