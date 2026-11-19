# Sourced by the setup scripts: finds the user's own Pixi and whether it is new
# enough for WGS Extract. install.sh makes the same choice.

# The user's own Pixi: one on PATH, then ~/.pixi's; nothing when there is none.
user_pixi() {
  if command -v pixi >/dev/null 2>&1; then
    command -v pixi
  elif [ -x "$HOME/.pixi/bin/pixi" ]; then
    printf '%s\n' "$HOME/.pixi/bin/pixi"
  fi
}

# The minimum Pixi in a pixi.toml (requires-pixi = ">=X.Y.Z"), or the first
# line of a text file holding just X.Y.Z; nothing when it declares none.
pixi_minimum_version() {
  sed -n \
    -e 's/^requires-pixi[[:space:]]*=[[:space:]]*">=\([0-9][0-9]*\.[0-9][0-9]*\.[0-9][0-9]*\)"[[:space:]]*$/\1/p' \
    -e '1s/^[[:space:]]*\([0-9][0-9]*\.[0-9][0-9]*\.[0-9][0-9]*\)[[:space:]]*$/\1/p' \
    "$1" 2>/dev/null | head -n 1
}

# The X.Y.Z version of the Pixi at $1, or nothing when it does not run.
pixi_version() {
  { "$1" --version 2>/dev/null || true; } |
    sed -n 's/^pixi \([0-9][0-9]*\.[0-9][0-9]*\.[0-9][0-9]*\).*$/\1/p' | head -n 1
}

# Whether Pixi version $1 is at least $2; an unknown minimum accepts any Pixi
# and an unknown version none.
pixi_version_at_least() {
  [ -z "$2" ] && return 0
  [ -n "$1" ] || return 1
  awk -v actual="$1" -v required="$2" 'BEGIN {
    split(actual, a, ".")
    split(required, r, ".")
    for (i = 1; i <= 3; i++) {
      if (a[i] + 0 > r[i] + 0) exit 0
      if (a[i] + 0 < r[i] + 0) exit 1
    }
    exit 0
  }'
}
