## Fixes

- The mockup server watchdog alerts when the server goes down or recovers, or
  when the failure reason changes
  ([#1451, watchdog alert spam](https://github.com/WGSExtract/wgsextract-cli/issues/1451)).
  It reminds agents every four hours during
  an ongoing outage instead of every five minutes.
