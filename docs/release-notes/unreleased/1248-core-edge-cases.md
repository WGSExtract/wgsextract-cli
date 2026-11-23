## Fixes

- Depth estimates and parallel variant calling now recognise plain-text CRAM
  indexes (`.crai`) as well as gzip-compressed indexes.
- Workflow steps now restore their inherited process tag, so nested workflows
  don't lose the tag used to identify tools from an interrupted run
  ([#1248, core edge cases](https://github.com/WGSExtract/wgsextract-cli/issues/1248)).
