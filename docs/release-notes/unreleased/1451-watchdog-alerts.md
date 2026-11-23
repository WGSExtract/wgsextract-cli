## Fixes

- The mockup server watchdog alerts when the server goes down or recovers, or
  when the failure reason changes. During an ongoing outage it reminds agents
  every four hours instead of every five minutes.
  See [#1451 (watchdog alert spam)](https://github.com/WGSExtract/wgsextract-cli/issues/1451).
