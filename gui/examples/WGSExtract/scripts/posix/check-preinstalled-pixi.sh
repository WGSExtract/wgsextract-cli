#!/bin/sh
# Reports which Pixi the WGS Extract installer will use: an explicit $PIXI,
# then one on PATH or in ~/.pixi; with none, it embeds Pixi in the bundle
# runtime. An incompatible user's Pixi is never updated without their consent.
set -eu

script_dir="$(CDPATH= cd "$(dirname "$0")" && pwd)"
minimum="$(cat "$script_dir/../pixi-minimum-version.txt")"
pixi=""
if [ -n "${PIXI:-}" ]; then
  if [ ! -x "$PIXI" ]; then
    printf 'Error: PIXI is set but is not executable: %s\n' "$PIXI" >&2
    exit 1
  fi
  pixi="$PIXI"
  printf 'Setup will use the Pixi set in PIXI: %s\n' "$PIXI"
elif command -v pixi >/dev/null 2>&1; then
  pixi="$(command -v pixi)"
  printf 'Setup will reuse the Pixi on PATH: %s\n' "$(command -v pixi)"
elif [ -x "$HOME/.pixi/bin/pixi" ]; then
  pixi="$HOME/.pixi/bin/pixi"
  printf 'Setup will reuse the Pixi in ~/.pixi: %s\n' "$HOME/.pixi/bin/pixi"
else
  printf 'Pixi is not pre-installed; setup will install it inside the bundle runtime.\n'
fi
if [ -n "$pixi" ]; then
  installed="$("$pixi" --version)" || {
    printf 'Error: Could not run Pixi at %s. Repair this installation and rerun setup.\n' "$pixi" >&2
    exit 1
  }
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
    printf "Error: Pixi >=%s is required; found %s at %s. Update that Pixi installation (for example with '%s self-update'), then rerun setup. Your Pixi installation was not changed.\n" "$minimum" "$installed" "$pixi" "$pixi" >&2
    exit 1
  fi
fi
