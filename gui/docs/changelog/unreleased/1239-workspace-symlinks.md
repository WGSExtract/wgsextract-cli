## Fixed

- Moving the users workspace or a library folder keeps the symbolic links in it working, on the same disk or to another one. A link to a file inside the moved folder now points at that file's new place (a relative link stays relative), instead of at the old folder; a link to something outside the folder still points there, and is moved as a link, never copied in. Windows folder links fall back to junctions when symbolic links aren't allowed ([#1239, links broken by a move](https://github.com/WGSExtract/wgsextract-cli/issues/1239)).
