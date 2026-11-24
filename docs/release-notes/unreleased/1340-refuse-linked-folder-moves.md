## Desktop app

- Settings refuses to move a workspace or library from or to a symbolic link or
  Windows junction. The error names the link and its target. To move a linked
  folder, move its target yourself and update the setting. If the destination is
  linked, select the actual folder instead. Links inside the folder being moved
  still work ([#1340, moving a linked folder](https://github.com/WGSExtract/wgsextract-cli/issues/1340)).
