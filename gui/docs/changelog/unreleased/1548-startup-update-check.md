## Added

- **Privacy mode** under Settings → Network & Privacy pauses the app's
  automatic network activity. While it is on, a red **PRIVACY** badge shows
  under the app's name; its tooltip says where to turn it off
  ([#1548](https://github.com/WGSExtract/wgsextract-cli/pull/1548)).

## Changed

- When **Check for updates at launch** is enabled and Privacy mode is off,
  the desktop app offers newer releases in the sidebar. A failed startup
  check shows nothing. **File → Check for Updates…** still checks on request.
- Running the app from a source checkout (`make run`, `make tauri`,
  `npm run dev`) keeps its own data folder, `WGSExtract-gui-dev`, so it no
  longer replaces the installed app's copy of the bundle or its CLI runtime.
  It also keeps its own users workspace, `~/wgs-extract-dev`.
