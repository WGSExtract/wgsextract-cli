## Changed

- `wgsextract --version` and `--help` start faster by loading configuration
  without initializing the command runtime. Configuration is still validated.
