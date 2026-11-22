## Fixes

- On Windows, `sign verify` now checks for a working OpenSSH verifier and can
  use Git for Windows when the inbox OpenSSH hangs. If no working verifier is
  available, the error identifies the builds tried and how to use a working one
  ([#1358, Windows signature verification hang](https://github.com/WGSExtract/wgsextract-cli/issues/1358)).
