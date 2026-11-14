#!/bin/sh
# Check without loading the manifest: older Pixi may not parse its syntax.
set -eu
ROOT="$(CDPATH='' cd "$(dirname "$0")/.." && pwd)"
minimum=$(sed -n 's/^requires-pixi = ">=\([0-9][0-9.]*\)"$/\1/p' "$ROOT/pixi.toml")
if [ -z "$minimum" ]; then
    printf 'ERROR: Cannot read the minimum Pixi version from pixi.toml.\n' >&2
    exit 1
fi
installed=$(pixi --version) || exit 1
if ! printf '%s\n' "$installed" | awk -v minimum="$minimum" '
    /^pixi [0-9]+\.[0-9]+\.[0-9]+$/ {
        split($2, actual, ".")
        split(minimum, required, ".")
        for (i = 1; i <= 3; i++) {
            if (actual[i] + 0 > required[i] + 0) exit 0
            if (actual[i] + 0 < required[i] + 0) exit 1
        }
        exit 0
    }
    { exit 1 }
'; then
    printf "ERROR: Pixi >=%s is required; found %s. Run 'pixi self-update', then rerun setup.\n" "$minimum" "$installed" >&2
    exit 1
fi
printf '%s (required >=%s)\n' "$installed" "$minimum"
