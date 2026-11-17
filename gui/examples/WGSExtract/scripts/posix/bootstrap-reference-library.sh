#!/bin/sh
set -eu

script_dir="$(CDPATH= cd "$(dirname "$0")" && pwd)"
# Into the settings file's reference_library (in the users workspace), unless a
# folder is named here.
if [ -n "${WGSEXTRACT_REFERENCE_LIBRARY:-}" ]; then
  exec sh "$script_dir/run-wgsextract.sh" ref bootstrap --ref "$WGSEXTRACT_REFERENCE_LIBRARY"
fi
exec sh "$script_dir/run-wgsextract.sh" ref bootstrap
