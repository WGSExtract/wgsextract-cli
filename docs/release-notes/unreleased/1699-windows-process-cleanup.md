## Fixes

- On Windows, cancelling a command stops subprocesses that outlive their
  launcher, so an inherited stderr pipe no longer holds up cleanup
  ([#1699, Windows process cleanup](https://github.com/WGSExtract/wgsextract-cli/issues/1699)).
