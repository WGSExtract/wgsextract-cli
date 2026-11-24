## Fixed

- Settings refuses a move if the user's workspace, the library folder being moved, or the chosen destination is itself a symbolic link or Windows junction. The error names the link and its target; nothing is moved or changed. To relocate a linked folder, move its target manually and update the setting. For a linked destination, select the actual folder instead of its link. Links inside the folder being moved are unaffected ([#1340, moving a linked folder](https://github.com/WGSExtract/wgsextract-cli/issues/1340)).
