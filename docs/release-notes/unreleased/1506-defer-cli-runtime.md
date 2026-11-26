## Changed

- `wgsextract --version` and `wgsextract --help` skip imports for run logging,
  worker pools, and result schemas when those features are not used. They still
  validate config and check for workspace moves
  ([#1506, CLI startup](https://github.com/WGSExtract/wgsextract-cli/issues/1506)).
