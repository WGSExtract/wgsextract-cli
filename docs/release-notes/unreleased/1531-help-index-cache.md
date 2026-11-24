## Changed

- Compact CLI discovery (`wgsextract help --all --json`) now uses a bundled
  build artifact or a per-user cache. If the index is missing or outdated,
  discovery rebuilds it locally without modifying the installation.
