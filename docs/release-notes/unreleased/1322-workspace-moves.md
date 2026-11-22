## Desktop app

- Moving the users workspace or a folder named in Settings now asks you to
  retry if a command or save is still running, instead of risking lost changes
  or output in the old folder. The CLI also follows an old `--config` path
  when the app moved that file and the old path no longer exists.
  ([#1322, workspace move coordination](https://github.com/WGSExtract/wgsextract-cli/issues/1322))
