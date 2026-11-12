#!/bin/sh
# Removes the WGS Extract CLI runtime that setup installed in the app's data
# folder, and one an earlier version installed in the workspace.
#
# The runtime's own uninstall.sh (written by the CLI's install.sh) knows every
# path the install created, so it does the work. The Pixi it used lives inside
# the runtime when setup installed it, and is a user's own Pixi otherwise, so
# Pixi is never removed here.
set -eu

script_dir="$(CDPATH= cd "$(dirname "$0")" && pwd)"
bundle_root="${WGSEXTRACT_GUI_BUNDLE_WORKSPACE:-$(CDPATH= cd "$script_dir/../.." && pwd)}"
legacy_runtime="$bundle_root/runtime/wgsextract-cli"

remove_runtime() {
  runtime="$1"
  if [ ! -e "$runtime" ]; then
    printf 'WGS Extract runtime is not installed: %s\n' "$runtime"
    return 0
  fi

  # Stop anything still running from the runtime before removing it.
  if command -v pkill >/dev/null 2>&1; then
    pkill -f "$runtime/" 2>/dev/null || true
  fi

  if [ -x "$runtime/uninstall.sh" ]; then
    "$runtime/uninstall.sh" --yes
  else
    # A setup that failed before install.sh finished leaves no uninstaller.
    rm -rf "$runtime"
  fi
  rmdir "$(dirname "$runtime")" 2>/dev/null || true

  [ ! -e "$runtime" ] || { printf 'Could not remove %s\n' "$runtime" >&2; exit 1; }
  printf 'Removed WGS Extract runtime: %s\n' "$runtime"
}

if [ -n "${WGSEXTRACT_GUI_APP_DATA:-}" ]; then
  remove_runtime "$WGSEXTRACT_GUI_APP_DATA/runtime/wgsextract-cli"
fi
remove_runtime "$legacy_runtime"
