## Fixed

- Two settings saves at once, from two windows or from the app and the CLI, no longer lose one of the changes: each save takes a lock the CLI shares and merges into the file as it is ([#1230, lost concurrent settings saves](https://github.com/WGSExtract/wgsextract-cli/issues/1230)).
