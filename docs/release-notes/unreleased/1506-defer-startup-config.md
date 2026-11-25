## Changed

- The CLI and the desktop app’s direct CLI launcher start faster by loading
  settings and resource helpers only when needed. Local `info` no longer loads
  the download stack. Config validation and workspace move checks still run
  ([#1506, CLI startup](https://github.com/WGSExtract/wgsextract-cli/issues/1506)).
