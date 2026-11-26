#!/bin/sh
# Runs COMMAND in the WGS Extract Pixi environment installed by setup.
# Pixi resolution: an explicit $PIXI, then the Pixi embedded in the bundle
# runtime, then a Pixi on PATH or in ~/.pixi (which setup's installer reuses
# instead of embedding one when present).
set -eu

if [ "$#" -lt 1 ]; then
  printf 'Usage: %s COMMAND [ARG...]\n' "$0" >&2
  exit 64
fi

script_dir="$(CDPATH= cd "$(dirname "$0")" && pwd)"
# Setup installs the runtime in the app's data folder; run outside the app, the
# workspace's own runtime folder (where apps up to 0.8.6 installed it).
if [ -n "${WGSEXTRACT_GUI_APP_DATA:-}" ]; then
  runtime="$WGSEXTRACT_GUI_APP_DATA/runtime/wgsextract-cli"
else
  runtime="$(CDPATH= cd "$script_dir/../.." && pwd)/runtime/wgsextract-cli"
fi
app_dir="${WGSEXTRACT_APP_DIR:-$runtime/app}"

if [ ! -d "$app_dir" ]; then
  printf 'Error: WGS Extract bundle runtime is not installed at %s.\n' "$app_dir" >&2
  printf 'Run the WGS Extract setup action before running commands.\n' >&2
  exit 127
fi
case "$app_dir" in
  /*) ;;
  *) app_dir="$(CDPATH= cd "$app_dir" && pwd)" ;;
esac

# Pixi's no-install mode otherwise falls back to programs on the host's PATH.
prefix="$app_dir/.pixi/envs/default"
if [ ! -f "$prefix/conda-meta/pixi" ]; then
  printf 'Error: WGS Extract CLI setup has not finished: its Pixi environment in %s is not installed.\n' "$app_dir" >&2
  printf 'Run the WGS Extract setup action again.\n' >&2
  exit 127
fi

export PIXI_CACHE_DIR="${PIXI_CACHE_DIR:-$runtime/.pixi/cache}"
if [ -n "${WGSEXTRACT_GUI_APP_DATA:-}" ]; then
  export RATTLER_CACHE_DIR="$runtime/.pixi/cache/rattler"
  export XDG_CACHE_HOME="$runtime/.cache"
fi
activation="$app_dir/.pixi/wgsextract-activation.json"
pixi=""
if [ -n "${PIXI:-}" ]; then
  if [ ! -x "$PIXI" ]; then
    printf 'Error: PIXI is set but is not executable: %s\n' "$PIXI" >&2
    exit 127
  fi
  pixi="$PIXI"
elif [ -x "$runtime/.pixi/bin/pixi" ]; then
  pixi="$runtime/.pixi/bin/pixi"
  # The embedded Pixi keeps its state in the runtime, not in ~/.pixi (see setup).
  export PIXI_HOME="$runtime/.pixi"
elif command -v pixi >/dev/null 2>&1; then
  pixi="$(command -v pixi)"
elif [ -x "$HOME/.pixi/bin/pixi" ]; then
  pixi="$HOME/.pixi/bin/pixi"
fi
if [ -n "${WGSEXTRACT_GUI_APP_DATA:-}" ]; then
  export PIXI_HOME="$runtime/.pixi"
fi

if [ "$1" = wgsextract ] && [ -z "${PIXI:-}" ] &&
   [ -x "$prefix/bin/python" ] && [ -f "$activation" ]; then
  shift
  cd "$app_dir"
  exec "$prefix/bin/python" "$script_dir/../installed_cli.py" launch "$activation" "${pixi:--}" "$@"
fi

if [ -z "$pixi" ]; then
  printf 'Error: Pixi was not found. Run the WGS Extract setup action again.\n' >&2
  exit 127
fi

cd "$app_dir"
if [ "$1" = --cache-activation ]; then
  # Capture outside the activated environment: shell-hook reports the changes
  # it makes, so invoking it inside `pixi run` would omit most of the settings.
  "$pixi" shell-hook --json --frozen |
    "$prefix/bin/python" "$script_dir/../installed_cli.py" capture "$activation"
  exit $?
fi
exec "$pixi" run --no-install --frozen "$@"
