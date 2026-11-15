#!/bin/sh
# Explicit lab provisioning, equivalent to the user's optional install command.
# Usage: install-optional-tools.sh DIR...
# Each DIR is searched in order for the installed CLI (*/app/pixi.toml): apps
# after 0.8.6 install it in their app data folder (WGSExtract-gui), apps since
# PR #531 in the workspace, older apps in their identifier's app data.
set -eu
manifest=""
for support in "$@"; do
    [ -d "$support" ] || continue
    manifest="$(find "$support" -path '*/app/pixi.toml' -type f | head -n 1)"
    [ -z "$manifest" ] || break
done
[ -n "$manifest" ] || { echo "No installed CLI manifest under: $*" >&2; exit 1; }
install_dir="$(dirname "$(dirname "$manifest")")"
pixi="$install_dir/.pixi/bin/pixi"
if [ ! -x "$pixi" ]; then
    pixi="$(command -v pixi || printf '%s' "$HOME/.pixi/bin/pixi")"
fi
PIXI_CACHE_DIR="$install_dir/.pixi/cache" \
RATTLER_CACHE_DIR="$install_dir/.pixi/cache/rattler" \
    "$pixi" install --locked --manifest-path "$manifest" -e gatk
