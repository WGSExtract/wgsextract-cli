## Fixes

- An interrupted reference download no longer resumes onto a different
  version of the file. Resumes were tied to the URL alone, so a file replaced
  at the same URL by one of the same size was installed as the old file's start
  and the new file's end. Both one-connection and segmented downloads now
  record the server's ETag or Last-Modified date, resume only that version and
  otherwise start over. A partial left by an earlier release, or from a server
  that names no version and without a pinned checksum, downloads again from
  the start
  ([#1225, resumed downloads mixing file versions](https://github.com/WGSExtract/wgsextract-cli/issues/1225)).
