## Fixed

- A run, setup or workspace move whose window disconnected before its progress stream began no longer starts, instead of running (and holding the workspace) with nobody listening; a progress write waiting on a stalled window always settles when the stream is cancelled ([#1234, backpressured progress on disconnect](https://github.com/WGSExtract/wgsextract-cli/issues/1234)).
