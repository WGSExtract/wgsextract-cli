## Added

- **Privacy mode** under Settings → Standard UI Options stops the app's
  automatic network activity. While it is on, a red **PRIVACY** badge shows
  under the app's name; its tooltip says where to turn it off.

## Changed

- The desktop app checks for a newer release when it starts and offers it from
  the sidebar, unless Privacy mode is on. A failed check (when offline, say)
  shows nothing. **File → Check for Updates…** works as before.
- Running the app from a source checkout (`make run`, `make tauri`,
  `npm run dev`) keeps its own data folder, `WGSExtract-gui-dev`, so it no
  longer replaces the installed app's copy of the bundle or its CLI runtime.
  Both use the default users workspace unless it is moved in one of them.
