## Fixes

- Cancelling a command now stops its subprocess tree. `ref bootstrap` no longer
  retries a cancelled download, and `ref gnomad` disconnects stalled downloads
  on cancellation ([#1213, cancellation cleanup](https://github.com/WGSExtract/wgsextract-cli/issues/1213)).
