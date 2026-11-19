#!/bin/sh
# Reports which Pixi the WGS Extract installer will use: an explicit $PIXI,
# then one on PATH or in ~/.pixi when it is new enough; otherwise it embeds
# Pixi in the bundle runtime. Fails only for an unusable $PIXI, which the
# installer rejects too.
set -eu

script_dir="$(CDPATH= cd "$(dirname "$0")" && pwd)"
. "$script_dir/pixi-version.sh"
# The bundled release's requires-pixi; setup checks the downloaded pixi.toml again.
minimum="$(pixi_minimum_version "$script_dir/../wgsextract-pixi-minimum.txt")"

if [ -n "${PIXI:-}" ]; then
  if [ ! -x "$PIXI" ]; then
    printf 'Error: PIXI is set but is not executable: %s\n' "$PIXI" >&2
    exit 1
  fi
  version="$(pixi_version "$PIXI")"
  if ! pixi_version_at_least "$version" "$minimum"; then
    printf "Error: PIXI is set to Pixi %s (%s), but WGS Extract needs Pixi >=%s. Update it with 'pixi self-update', or unset PIXI.\n" \
      "${version:-of unknown version}" "$PIXI" "$minimum" >&2
    exit 1
  fi
  printf 'Setup will use the Pixi %s set in PIXI: %s\n' "$version" "$PIXI"
  exit 0
fi

pixi="$(user_pixi)"
if [ -z "$pixi" ]; then
  printf 'Pixi is not pre-installed; setup will install it inside the bundle runtime.\n'
  exit 0
fi
version="$(pixi_version "$pixi")"
case "$pixi" in
  "$HOME/.pixi/bin/pixi") where="in ~/.pixi" ;;
  *) where="on PATH" ;;
esac
if pixi_version_at_least "$version" "$minimum"; then
  printf 'Setup will reuse the Pixi %s %s: %s\n' "$version" "$where" "$pixi"
else
  printf 'The Pixi %s (%s) is %s, older than the >=%s WGS Extract needs; setup will install its own Pixi inside the bundle runtime and leave yours unchanged.\n' \
    "$where" "$pixi" "${version:-of unknown version}" "$minimum"
fi
