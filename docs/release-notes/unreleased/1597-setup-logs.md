## Fixes

- Installer checks no longer read the user's CLI config. Local archives
  are described as already downloaded, and desktop-managed installs omit
  terminal-only guidance. The CLI reports competing config files once per
  process and shows unchanged ploidy files only in debug output
  ([#1597, first-run setup output](https://github.com/WGSExtract/wgsextract-cli/issues/1597)).
