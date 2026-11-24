## Fixed

- Moving the users workspace or a folder named in Settings now asks you to
  retry if a command or save is still running, instead of risking lost changes
  or output in the old folder. A refused move does not interrupt the steps
  of an already-running CLI workflow.
  ([#1322, workspace move coordination](https://github.com/WGSExtract/wgsextract-cli/issues/1322))
