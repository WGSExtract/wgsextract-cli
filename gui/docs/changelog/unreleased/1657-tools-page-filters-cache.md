## Added

- The Tools page shows the computer the CLI runs on: operating system and
  version, architecture, CPU cores and threads, memory, and free disk space
  where WGS Extract keeps its files ([#1657, Tools page follow-ups](https://github.com/WGSExtract/wgsextract-cli/pull/1657)).

- The Tools page has a "Hide mandatory and pre-installed" checkbox beside its
  filter, checked by default. A mandatory or pre-installed tool that is
  missing stays in the list ([#1657, Tools page follow-ups](https://github.com/WGSExtract/wgsextract-cli/pull/1657)).

## Changed

- In Basic mode the Tools page leaves out tools that only Advanced features
  use ([#1657, Tools page follow-ups](https://github.com/WGSExtract/wgsextract-cli/pull/1657)).
- Tools, Reference Data and Reference Genomes come back at once when you
  return to them: their lists are kept until an install, download, uninstall
  or other change that affects them, or until the app restarts ([#1657, Tools page follow-ups](https://github.com/WGSExtract/wgsextract-cli/pull/1657)).
- A "not available" tool's row says why it can't be had on this computer.
  A tool's tooltip no longer repeats the row's note ([#1657, Tools page follow-ups](https://github.com/WGSExtract/wgsextract-cli/pull/1657)).

## Fixed

- Switching between Basic and Advanced always redraws the page, even when
  saving the choice fails ([#1657, Tools page follow-ups](https://github.com/WGSExtract/wgsextract-cli/pull/1657)).
