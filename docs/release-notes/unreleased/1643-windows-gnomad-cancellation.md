## Fixes

- Cancelling a stalled `ref gnomad` download on Windows no longer waits for
  shutdown grace periods intended for Unix processes
  ([#1643, slow gnomAD cancellation](https://github.com/WGSExtract/wgsextract-cli/issues/1643)).
