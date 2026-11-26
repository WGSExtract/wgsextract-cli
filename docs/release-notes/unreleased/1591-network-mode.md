## New

- Set `network_mode` in config.toml or pass `--network full|low|off` for one
  run. Low mode requires `--allow-large-download` for bulk transfers; off mode
  blocks network operations even when a download was requested. Existing
  download and disclosure opt-ins still apply.
