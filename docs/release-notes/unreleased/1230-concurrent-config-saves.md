## Fixes

- Saving settings from the desktop app and from `wgsextract` at the same
  time no longer loses one of the changes: each save of config.toml now waits
  for the other and merges into the file as it is, also when the file is
  reached through a symlinked folder
  ([#1230, lost concurrent config saves](https://github.com/WGSExtract/wgsextract-cli/issues/1230)).
