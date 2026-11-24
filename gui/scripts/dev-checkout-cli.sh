#!/bin/sh
# Serves the Web UI in dev mode (TypeScript watch, server restart, browser
# reload) with the WGSExtract bundle running the CLI from this checkout, so a
# browser refresh picks up both GUI and CLI edits.
#   sh gui/scripts/dev-checkout-cli.sh [PORT]
set -eu
port="${1:-8787}"
repo="$(CDPATH= cd "$(dirname "$0")/../.." && pwd)"
if [ ! -f "$repo/.pixi/envs/default/conda-meta/pixi" ]; then
  printf "This checkout's Pixi environment is not installed. Run 'pixi install' in %s first.\n" "$repo" >&2
  exit 1
fi
# The bundle's POSIX runner takes the checkout as its app folder directly.
WGSEXTRACT_APP_DIR="$repo"
export WGSEXTRACT_APP_DIR
printf 'CLI: %s\n' "$repo"
cd "$repo/gui/app"
exec npm run dev -- --bundle ../examples/WGSExtract --port "$port"
