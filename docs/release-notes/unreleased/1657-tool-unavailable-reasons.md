## Changed

- `deps list`, `deps install` and the desktop app say why a tool isn't
  available on this computer, not just that it isn't built for it: for
  example, that granite loads pysam and pytabix, which have no Windows builds.
  Platforms are named as people say them ("Windows", "macOS on Apple silicon",
  "Linux ARM64"), never "win32" ([#1657, Tools page follow-ups](https://github.com/WGSExtract/wgsextract-cli/pull/1657)).

## New

- `deps list` rows list `used_by`, the commands whose runs need or may use
  each tool ([#1657, Tools page follow-ups](https://github.com/WGSExtract/wgsextract-cli/pull/1657)).
