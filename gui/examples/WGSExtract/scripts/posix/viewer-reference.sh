#!/bin/sh
set -eu
script_dir="$(CDPATH= cd "$(dirname "$0")" && pwd)"
exec sh "$script_dir/run-wgsextract-env.sh" python "$script_dir/../viewer_reference.py" "$@"
