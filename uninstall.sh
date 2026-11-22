#!/bin/sh
# shellcheck disable=SC1007
# SC1007: `CDPATH= cd "$dir"` intentionally clears CDPATH for one command.
set -eu

log() {
    printf '%s\n' "$*"
}

fail() {
    printf 'Error: %s\n' "$*" >&2
    exit 1
}

absolute_path() {
    case "$1" in
        /*)
            printf '%s\n' "$1"
            ;;
        *)
            printf '%s/%s\n' "$(pwd -P)" "$1"
            ;;
    esac
}

script_dir() {
    script_path="${0:-}"
    script_name="$(basename "$script_path")"

    case "$script_name" in
        sh|bash|dash|zsh|ksh|-*)
            pwd -P
            return
            ;;
    esac

    if [ -n "$script_path" ] && [ -f "$script_path" ]; then
        dir_name="$(dirname "$script_path")"
        (CDPATH= cd "$dir_name" && pwd -P) || pwd -P
        return
    fi

    pwd -P
}

default_install_dir() {
    dir="$(script_dir)"
    base="$(basename "$dir")"
    parent="$(dirname "$dir")"

    if [ "$base" = "app" ] && [ -f "$dir/pixi.toml" ]; then
        printf '%s\n' "$parent"
        return
    fi

    if [ -d "$dir/app" ] && [ -f "$dir/app/pixi.toml" ]; then
        printf '%s\n' "$dir"
        return
    fi

    printf '%s/wgsextract-cli\n' "$dir"
}

usage() {
    cat <<'EOF'
WGS Extract CLI uninstaller for macOS and Linux installs created by install.sh.

Usage:
  uninstall.sh [options]

Options:
  --yes, -y                 Do not prompt for confirmation.
  --install-dir PATH        Install directory to remove.
  --bin-dir PATH            Directory containing the wgsextract launcher.
  --pixi-cache-dir PATH     Pixi cache directory used by the install.
  --pixi-env-dir PATH       Pixi environment directory used by the install.
  --keep-pixi-cache         Leave the Pixi cache directory in place.
  --keep-pixi-envs          Leave the Pixi environment directory in place.
  --remove-pixi             Also remove Pixi from ~/.pixi without prompting.
  --keep-pixi               Do not ask about removing Pixi.
  --remove-config           Also remove WGS Extract config.toml.
  --dry-run                 Print resolved paths and exit without changing anything.
  --help, -h                Show this help.

The uninstaller removes the WGS Extract install tree and launchers. Outside the
install, it removes only this install's environments from a Pixi envs folder,
and a Pixi cache folder only if the installer created it. Interactive
runs ask whether to remove Pixi from ~/.pixi too; noninteractive --yes runs keep
Pixi unless --remove-pixi is also set.
EOF
}

need_value() {
    [ "$#" -gt 1 ] || fail "$1 requires a path argument."
}

is_within_or_same() {
    child="$1"
    parent="$2"
    case "$child" in
        "$parent"|"$parent"/*)
            return 0
            ;;
        *)
            return 1
            ;;
    esac
}

remove_file() {
    remove_file_target="$1"
    if [ ! -e "$remove_file_target" ] && [ ! -L "$remove_file_target" ]; then
        log "Not found: $remove_file_target"
        return
    fi
    log "Removing file: $remove_file_target"
    rm -f "$remove_file_target"
}

remove_launcher_file() {
    launcher_target="$1"
    if [ ! -e "$launcher_target" ] && [ ! -L "$launcher_target" ]; then
        log "Not found: $launcher_target"
        return
    fi
    if has_symlink_component "$launcher_target"; then
        fail "Refusing to remove launcher through a symlink: $launcher_target"
    fi
    if ! grep -F "# WGS Extract CLI installer launcher" "$launcher_target" >/dev/null 2>&1; then
        fail "Refusing to remove launcher that does not look like WGS Extract CLI: $launcher_target"
    fi
    remove_file "$launcher_target"
}

remove_dir() {
    remove_dir_target="$1"
    if [ ! -e "$remove_dir_target" ]; then
        log "Not found: $remove_dir_target"
        return
    fi
    validate_removal_path "$remove_dir_target"
    if [ ! -d "$remove_dir_target" ]; then
        fail "Expected a directory but found something else: $remove_dir_target"
    fi
    log "Removing directory: $remove_dir_target"
    rm -rf "$remove_dir_target"
}

remove_pixi_home() {
    if [ ! -e "$USER_PIXI_HOME" ]; then
        log "Pixi was not found at: $USER_PIXI_HOME"
        return
    fi
    if [ "$USER_PIXI_HOME" != "$HOME/.pixi" ]; then
        fail "Refusing to remove unexpected Pixi home: $USER_PIXI_HOME"
    fi
    if has_symlink_component "$USER_PIXI_HOME"; then
        fail "Refusing to remove Pixi through a symlink: $USER_PIXI_HOME"
    fi
    log "Removing Pixi directory: $USER_PIXI_HOME"
    rm -rf "$USER_PIXI_HOME" || fail "Failed to remove Pixi directory: $USER_PIXI_HOME"
}

remove_empty_dir() {
    remove_empty_dir_target="$1"
    rmdir "$remove_empty_dir_target" 2>/dev/null || true
}

trim_trailing_slashes() {
    trim_path_input="$1"
    while [ "$trim_path_input" != "/" ] && [ "${trim_path_input%/}" != "$trim_path_input" ]; do
        trim_path_input="${trim_path_input%/}"
    done
    printf '%s\n' "$trim_path_input"
}

has_symlink_component() {
    symlink_path="$(trim_trailing_slashes "$1")"
    while [ -n "$symlink_path" ] && [ "$symlink_path" != "/" ] && [ "$symlink_path" != "." ]; do
        if [ -L "$symlink_path" ]; then
            return 0
        fi
        symlink_parent="$(dirname "$symlink_path")"
        [ "$symlink_parent" = "$symlink_path" ] && break
        symlink_path="$symlink_parent"
    done
    return 1
}

physical_path() {
    physical_input="$1"
    if [ -d "$physical_input" ]; then
        (CDPATH= cd "$physical_input" && pwd -P) && return
    fi

    physical_dir="$(dirname "$physical_input")"
    physical_base="$(basename "$physical_input")"
    if [ -d "$physical_dir" ]; then
        physical_resolved_dir="$(CDPATH= cd "$physical_dir" && pwd -P)" || return 1
        printf '%s/%s\n' "$physical_resolved_dir" "$physical_base"
        return
    fi

    printf '%s\n' "$physical_input"
}

validate_removal_path() {
    validation_target="$(trim_trailing_slashes "$1")"
    if has_symlink_component "$validation_target"; then
        fail "Refusing to remove directory through a symlink: $1"
    fi
    validation_physical_target="$(physical_path "$validation_target")"
    case "$validation_physical_target" in
        ""|"."|".."|"/"|"$HOME"|"$HOME/.pixi"|"/Applications"|"/Library"|"/System"|"/Users"|"/bin"|"/etc"|"/opt"|"/opt/homebrew"|"/private"|"/private/tmp"|"/private/var/tmp"|"/sbin"|"/tmp"|"/usr"|"/usr/bin"|"/usr/local"|"/var"|"/var/tmp")
            fail "Refusing to remove unsafe directory: $1"
            ;;
    esac
}

validate_install_marker() {
    if [ -d "$INSTALL_DIR" ] && [ ! -f "$INSTALL_DIR/app/pixi.toml" ]; then
        fail "$INSTALL_DIR does not look like a WGS Extract CLI install created by install.sh."
    fi
}

# Pixi keeps a project's detached environments in <env dir>/<name>-<hash>/envs
# and links app/.pixi/envs there. Other projects may share the env dir, so only
# this install's folder is ever removed from it.
project_env_dir() {
    env_link="$INSTALL_DIR/app/.pixi/envs"
    [ -L "$env_link" ] || return 0
    env_target="$(CDPATH= cd -P "$env_link" 2>/dev/null && pwd -P)" || return 0
    [ "$(basename "$env_target")" = "envs" ] || return 0
    env_project="$(dirname "$env_target")"
    [ "$(dirname "$env_project")" = "$(physical_path "$PIXI_ENV_DIR")" ] || return 0
    owned_by_install "$env_project" || return 0
    printf '%s/%s\n' "$(trim_trailing_slashes "$PIXI_ENV_DIR")" "$(basename "$env_project")"
}

# Pixi records each environment's manifest in conda-meta/pixi. A copied install
# can link to another install's folder, so require every environment there to
# name this install's manifest.
owned_by_install() {
    owned=1
    for env_meta in "$1"/envs/*/conda-meta/pixi; do
        [ -f "$env_meta" ] || continue
        if grep -Fq "\"manifest_path\": \"$(physical_path "$INSTALL_DIR")/app/pixi.toml\"" "$env_meta" ||
            grep -Fq "\"manifest_path\": \"$(trim_trailing_slashes "$INSTALL_DIR")/app/pixi.toml\"" "$env_meta"; then
            owned=0
        else
            return 1
        fi
    done
    return "$owned"
}

# Whether cache $1 holds a Pixi or conda environment (a conda-meta folder)
# outside $2. Pixi's own cached environments ($1/cached-envs-v0) are the cache's.
holds_environments() {
    [ -n "$(find "$1" \( -path "$2" -o -path "$1/cached-envs-v0" \) -prune -o -type d -name conda-meta -print 2>/dev/null | head -n 1)" ]
}

# Whether cache $1 holds the named or recorded Pixi envs folder.
holds_env_dir() {
    for held_env_dir in "$PIXI_ENV_DIR" "$DEFAULT_PIXI_ENV_DIR"; do
        held_env_dir="$(trim_trailing_slashes "$held_env_dir")"
        if is_within_or_same "$held_env_dir" "$1" ||
            is_within_or_same "$(physical_path "$held_env_dir")" "$(physical_path "$1")"; then
            return 0
        fi
    done
    return 1
}

refuse_symlinked_users_workspace() {
    if has_symlink_component "$HOME/wgs-extract"; then
        fail "Refusing to remove users workspace config through a symlink: $HOME/wgs-extract/config.toml"
    fi
}

# The users workspace config is removed only from a workspace marked as ours.
in_unmarked_users_workspace() {
    [ "$1" = "$HOME/wgs-extract/config.toml" ] || return 1
    users_marker="$HOME/wgs-extract/.users-workspace"
    [ ! -f "$users_marker" ] || [ -L "$users_marker" ] || [ "$(cat "$users_marker")" != "WGS Extract users workspace" ]
}

config_paths() {
    printf '%s\n' "$HOME/wgs-extract/config.toml"
    case "$(uname -s)" in
        Darwin)
            if [ -n "${XDG_CONFIG_HOME:-}" ]; then
                printf '%s\n' "$XDG_CONFIG_HOME/wgsextract/config.toml"
            fi
            if [ -d "$HOME/.config" ]; then
                printf '%s\n' "$HOME/.config/wgsextract/config.toml"
            fi
            printf '%s\n' "$HOME/Library/Application Support/wgsextract/config.toml"
            ;;
        Linux)
            printf '%s\n' "${XDG_CONFIG_HOME:-$HOME/.config}/wgsextract/config.toml"
            ;;
        *)
            ;;
    esac
}

remove_empty_users_workspace_marker() {
    workspace_dir="$1"
    [ "$workspace_dir" = "$HOME/wgs-extract" ] || return 0
    workspace_marker="$workspace_dir/.users-workspace"
    [ -f "$workspace_marker" ] && [ ! -L "$workspace_marker" ] || return 0
    [ "$(cat "$workspace_marker")" = "WGS Extract users workspace" ] || return 0
    for workspace_entry in "$workspace_dir"/* "$workspace_dir"/.[!.]* "$workspace_dir"/..?*; do
        [ -e "$workspace_entry" ] || [ -L "$workspace_entry" ] || continue
        [ "$workspace_entry" = "$workspace_marker" ] || return 0
    done
    remove_file "$workspace_marker"
    if ! rmdir "$workspace_dir" 2>/dev/null; then
        # A concurrent writer may have populated it: keep its ownership marker.
        (set -C; printf '%s\n' "WGS Extract users workspace" > "$workspace_marker") 2>/dev/null || true
    fi
}

is_created_profile() {
    printf '%s\n' "$created_profiles" | grep -Fx -- "$1" >/dev/null 2>&1
}

remove_pixi_shell_profile_entries() {
    for profile_path in "$HOME/.profile" "$HOME/.bash_profile" "$HOME/.bashrc" "$HOME/.zshrc" "$HOME/.config/fish/config.fish"; do
        if [ ! -f "$profile_path" ]; then
            continue
        fi
        if [ -L "$profile_path" ]; then
            fail "Refusing to edit symlinked shell profile: $profile_path"
        fi
        profile_tmp="$profile_path.wgsextract-uninstall.$$"
        awk '
            /^# >>> pixi initialize >>>/ {
                in_pixi_block = 1
                next
            }
            /^# <<< pixi initialize <<</ && in_pixi_block {
                in_pixi_block = 0
                next
            }
            in_pixi_block {
                next
            }
            {
                path_line = $0
                comment_start = index(path_line, "#")
                if (comment_start > 0) {
                    path_line = substr(path_line, 1, comment_start - 1)
                }
            }
            index(path_line, ".pixi/bin") && (path_line ~ /^[[:space:]]*(export[[:space:]]+)?PATH=/ || path_line ~ /^[[:space:]]*set[[:space:]]+(-[-[:alnum:]]+[[:space:]]+)*PATH[[:space:]]/) {
                next
            }
            {
                print
            }
            END {
                if (in_pixi_block) {
                    exit 2
                }
            }
        ' "$profile_path" > "$profile_tmp" || {
            rm -f "$profile_tmp"
            fail "Could not safely update shell profile: $profile_path"
        }
        if cmp -s "$profile_path" "$profile_tmp"; then
            rm -f "$profile_tmp"
        elif is_created_profile "$profile_path" && ! grep -q '[^[:space:]]' "$profile_tmp"; then
            # The install created this profile and nothing else was added since.
            rm -f "$profile_tmp" "$profile_path"
            log "Removed shell profile created by the install: $profile_path"
            profile_parent="$(dirname "$profile_path")"
            while [ "$profile_parent" != "$HOME" ] && rmdir "$profile_parent" 2>/dev/null; do
                profile_parent="$(dirname "$profile_parent")"
            done
        else
            profile_backup="$profile_path.wgsextract-uninstall-backup.$(date +%Y%m%d%H%M%S).$$"
            cp -p "$profile_path" "$profile_backup" || {
                rm -f "$profile_tmp"
                fail "Could not back up shell profile before editing: $profile_path"
            }
            mv "$profile_tmp" "$profile_path" || {
                rm -f "$profile_tmp"
                fail "Could not update shell profile: $profile_path"
            }
            log "Removed Pixi PATH entries from: $profile_path"
            log "Backup saved as: $profile_backup"
        fi
    done
}

select_pixi_removal() {
    REMOVE_PIXI_SELECTED=0
    if [ "$PIXI_REMOVAL" = "keep" ]; then
        return
    fi
    if [ "$PIXI_REMOVAL" = "remove" ]; then
        REMOVE_PIXI_SELECTED=1
        return
    fi
    if [ ! -x "$USER_PIXI_HOME/bin/pixi" ]; then
        log "Pixi was not found at: $USER_PIXI_HOME"
        return
    fi
    if [ "$ASSUME_YES" = "1" ]; then
        log "Keeping Pixi. Pass --remove-pixi to remove it during a --yes uninstall."
        return
    fi

    log ""
    log "Pixi is installed at:"
    log "  $USER_PIXI_HOME"
    log "Pixi may be shared with other projects."
    printf 'Remove Pixi too? [y/N] '
    if ! read -r REMOVE_PIXI_CONFIRM; then
        log "Keeping Pixi."
        return
    fi
    case "$REMOVE_PIXI_CONFIRM" in
        y|Y)
            REMOVE_PIXI_SELECTED=1
            ;;
        *)
            log "Keeping Pixi."
            ;;
    esac
}

ASSUME_YES=0
DRY_RUN=0
KEEP_PIXI_CACHE=0
KEEP_PIXI_ENVS=0
REMOVE_CONFIG=0
PIXI_REMOVAL=ask
INSTALL_DIR_RAW="${WGSEXTRACT_INSTALL_DIR:-$(default_install_dir)}"
BIN_DIR_RAW="${WGSEXTRACT_BIN_DIR:-}"
PIXI_CACHE_DIR_RAW="${WGSEXTRACT_PIXI_CACHE_DIR:-}"
PIXI_ENV_DIR_RAW="${WGSEXTRACT_PIXI_ENV_DIR:-}"
REMOVE_PIXI_SELECTED=0

while [ "$#" -gt 0 ]; do
    case "$1" in
        --help|-h)
            usage
            exit 0
            ;;
        --yes|-y)
            ASSUME_YES=1
            ;;
        --dry-run)
            DRY_RUN=1
            ;;
        --keep-pixi-cache)
            KEEP_PIXI_CACHE=1
            ;;
        --keep-pixi-envs)
            KEEP_PIXI_ENVS=1
            ;;
        --remove-pixi)
            PIXI_REMOVAL=remove
            ;;
        --keep-pixi)
            PIXI_REMOVAL=keep
            ;;
        --remove-config)
            REMOVE_CONFIG=1
            ;;
        --install-dir)
            need_value "$@"
            shift
            INSTALL_DIR_RAW="$1"
            ;;
        --install-dir=*)
            INSTALL_DIR_RAW="${1#*=}"
            ;;
        --bin-dir)
            need_value "$@"
            shift
            BIN_DIR_RAW="$1"
            ;;
        --bin-dir=*)
            BIN_DIR_RAW="${1#*=}"
            ;;
        --pixi-cache-dir)
            need_value "$@"
            shift
            PIXI_CACHE_DIR_RAW="$1"
            ;;
        --pixi-cache-dir=*)
            PIXI_CACHE_DIR_RAW="${1#*=}"
            ;;
        --pixi-env-dir)
            need_value "$@"
            shift
            PIXI_ENV_DIR_RAW="$1"
            ;;
        --pixi-env-dir=*)
            PIXI_ENV_DIR_RAW="${1#*=}"
            ;;
        *)
            fail "Unknown option: $1. Run uninstall.sh --help for usage."
            ;;
    esac
    shift
done

if [ -z "${HOME:-}" ] || [ "$HOME" = "/" ]; then
    fail "HOME environment variable is not set or is unsafe."
fi

INSTALL_DIR="$(absolute_path "$INSTALL_DIR_RAW")"
DEFAULT_BIN_DIR="$INSTALL_DIR"
DEFAULT_PIXI_CACHE_DIR="$INSTALL_DIR/.pixi/cache"
DEFAULT_PIXI_ENV_DIR="$INSTALL_DIR/app/.pixi/envs"
CREATED_PIXI_CACHE_DIR=""
CREATED_PIXI_ENV_DIR=""
# install.sh records the directories it used; options and env vars still win.
if [ -f "$INSTALL_DIR/install-layout.txt" ]; then
    while IFS="$(printf '\t')" read -r layout_key layout_path; do
        case "$layout_path" in
            /*) ;;
            *) continue ;;
        esac
        case "$layout_key" in
            bin_dir) DEFAULT_BIN_DIR="$layout_path" ;;
            pixi_cache_dir) DEFAULT_PIXI_CACHE_DIR="$layout_path" ;;
            pixi_env_dir) DEFAULT_PIXI_ENV_DIR="$layout_path" ;;
            created_pixi_cache_dir) CREATED_PIXI_CACHE_DIR="$layout_path" ;;
            created_pixi_env_dir) CREATED_PIXI_ENV_DIR="$layout_path" ;;
        esac
    done < "$INSTALL_DIR/install-layout.txt"
fi
BIN_DIR="$(absolute_path "${BIN_DIR_RAW:-$DEFAULT_BIN_DIR}")"
LAUNCHER="$BIN_DIR/wgsextract"
PIXI_CACHE_DIR="$(absolute_path "${PIXI_CACHE_DIR_RAW:-$DEFAULT_PIXI_CACHE_DIR}")"
PIXI_ENV_DIR="$(absolute_path "${PIXI_ENV_DIR_RAW:-$DEFAULT_PIXI_ENV_DIR}")"
USER_PIXI_HOME="$HOME/.pixi"

validate_removal_path "$INSTALL_DIR"

# Decide what goes before asking, so the prompt names exactly what is deleted.
ENV_REMOVAL=""
ENV_NOTE=""
if is_within_or_same "$PIXI_ENV_DIR" "$INSTALL_DIR"; then
    if [ "$KEEP_PIXI_ENVS" = "1" ]; then
        ENV_NOTE="--keep-pixi-envs does not keep Pixi environments inside the install: $PIXI_ENV_DIR"
    fi
elif [ "$KEEP_PIXI_ENVS" = "1" ]; then
    ENV_NOTE="Keeping Pixi environments: $PIXI_ENV_DIR"
else
    ENV_REMOVAL="$(project_env_dir)"
    if [ -n "$ENV_REMOVAL" ]; then
        validate_removal_path "$ENV_REMOVAL"
        ENV_NOTE="Keeping anything else in the Pixi envs folder: $PIXI_ENV_DIR"
    else
        ENV_NOTE="Keeping the Pixi envs folder; this install's environments were not found in it: $PIXI_ENV_DIR"
    fi
fi
CACHE_REMOVAL=""
CACHE_NOTE=""
cache_dir_check="$(trim_trailing_slashes "$PIXI_CACHE_DIR")"
if is_within_or_same "$PIXI_CACHE_DIR" "$INSTALL_DIR"; then
    if [ "$KEEP_PIXI_CACHE" = "1" ]; then
        CACHE_NOTE="--keep-pixi-cache does not keep a Pixi cache inside the install: $PIXI_CACHE_DIR"
    fi
elif [ "$KEEP_PIXI_CACHE" = "1" ]; then
    CACHE_NOTE="Keeping Pixi cache: $PIXI_CACHE_DIR"
elif [ -z "$CREATED_PIXI_CACHE_DIR" ] ||
    [ "$cache_dir_check" != "$(trim_trailing_slashes "$CREATED_PIXI_CACHE_DIR")" ]; then
    CACHE_NOTE="Keeping the Pixi cache, which the installer did not create and other projects may share: $PIXI_CACHE_DIR"
else
    validate_removal_path "$PIXI_CACHE_DIR"
    if [ "$KEEP_PIXI_ENVS" = "1" ] && holds_env_dir "$cache_dir_check"; then
        CACHE_NOTE="Keeping the Pixi cache, which holds the kept Pixi envs folder: $PIXI_CACHE_DIR"
    elif holds_environments "$cache_dir_check" "$ENV_REMOVAL"; then
        # Whichever env dir was named or recorded, never delete other environments.
        CACHE_NOTE="Keeping the Pixi cache, which holds Pixi environments: $PIXI_CACHE_DIR"
    else
        CACHE_REMOVAL="$PIXI_CACHE_DIR"
    fi
fi
# Removing this install's environments must not take a kept cache with them.
if [ -n "$ENV_REMOVAL" ] && [ -z "$CACHE_REMOVAL" ] &&
    ! is_within_or_same "$PIXI_CACHE_DIR" "$INSTALL_DIR" &&
    { is_within_or_same "$cache_dir_check" "$ENV_REMOVAL" ||
        is_within_or_same "$(physical_path "$cache_dir_check")" "$(physical_path "$ENV_REMOVAL")"; }; then
    ENV_NOTE="Keeping this install's Pixi environments, which hold the kept Pixi cache: $ENV_REMOVAL"
    ENV_REMOVAL=""
fi
if [ "$REMOVE_CONFIG" = "1" ]; then
    refuse_symlinked_users_workspace
fi

log "--- WGS Extract CLI uninstaller ---"
log "Install directory: $INSTALL_DIR"
log "Launcher:          $LAUNCHER"
log "Pixi envs:         $PIXI_ENV_DIR"
log "Pixi cache:        $PIXI_CACHE_DIR"
log "Pixi home:         $USER_PIXI_HOME"
if [ "$REMOVE_CONFIG" = "1" ]; then
    log "Config removal:    enabled"
else
    log "Config removal:    disabled"
fi
case "$PIXI_REMOVAL" in
    remove)
        log "Pixi removal:      enabled"
        ;;
    keep)
        log "Pixi removal:      disabled"
        ;;
    *)
        log "Pixi removal:      ask"
        ;;
esac
log ""
log "This removes:"
log "  $INSTALL_DIR (the WGS Extract CLI install)"
if ! is_within_or_same "$LAUNCHER" "$INSTALL_DIR"; then
    log "  $LAUNCHER (launcher)"
fi
if [ -n "$ENV_REMOVAL" ]; then
    log "  $ENV_REMOVAL (this install's Pixi environments)"
fi
if [ -n "$CACHE_REMOVAL" ]; then
    log "  $CACHE_REMOVAL (the Pixi cache the installer created)"
fi
if [ "$REMOVE_CONFIG" = "1" ]; then
    while IFS= read -r config_path; do
        if { [ -e "$config_path" ] || [ -L "$config_path" ]; } &&
            ! in_unmarked_users_workspace "$config_path"; then
            log "  $config_path (WGS Extract config)"
        fi
    done <<EOF
$(config_paths)
EOF
fi
if [ "$PIXI_REMOVAL" = "remove" ] && { [ -e "$USER_PIXI_HOME" ] || [ -L "$USER_PIXI_HOME" ]; }; then
    log "  $USER_PIXI_HOME (Pixi, and its PATH lines in shell profiles)"
fi
for removal_note in "$ENV_NOTE" "$CACHE_NOTE"; do
    if [ -n "$removal_note" ]; then
        log "$removal_note"
    fi
done
log ""

if [ "$DRY_RUN" = "1" ]; then
    log "Dry run only; no changes were made."
    exit 0
fi

validate_install_marker

if [ "$ASSUME_YES" = "0" ]; then
    printf 'Continue? [y/N] '
    if ! read -r CONFIRM; then
        log "Uninstall cancelled."
        exit 0
    fi
    case "$CONFIRM" in
        y|Y)
            ;;
        *)
            log "Uninstall cancelled."
            exit 0
            ;;
    esac
fi

select_pixi_removal
validate_install_marker

if is_within_or_same "$LAUNCHER" "$INSTALL_DIR"; then
    log "Launcher is inside the install directory and will be removed with it."
else
    remove_launcher_file "$LAUNCHER"
    remove_empty_dir "$BIN_DIR"
fi

if [ -n "$ENV_REMOVAL" ]; then
    remove_dir "$ENV_REMOVAL"
fi
if [ "$KEEP_PIXI_ENVS" = "0" ] && [ -n "$CREATED_PIXI_ENV_DIR" ] &&
    [ "$(trim_trailing_slashes "$PIXI_ENV_DIR")" = "$(trim_trailing_slashes "$CREATED_PIXI_ENV_DIR")" ]; then
    remove_empty_dir "$PIXI_ENV_DIR"
fi
if [ -n "$CACHE_REMOVAL" ]; then
    remove_dir "$CACHE_REMOVAL"
fi

created_dirs=""
if [ -f "$INSTALL_DIR/created-dirs.txt" ]; then
    created_dirs="$(cat "$INSTALL_DIR/created-dirs.txt")"
fi
created_profiles=""
if [ -f "$INSTALL_DIR/created-profiles.txt" ]; then
    created_profiles="$(cat "$INSTALL_DIR/created-profiles.txt")"
fi

remove_dir "$INSTALL_DIR"

if [ "$REMOVE_CONFIG" = "1" ]; then
    # Loop in this shell, not a pipeline subshell, so `fail` exits the uninstaller.
    while IFS= read -r config_path; do
        if [ "$config_path" = "$HOME/wgs-extract/config.toml" ]; then
            refuse_symlinked_users_workspace
            if in_unmarked_users_workspace "$config_path"; then
                log "Keeping config in an unmarked users workspace: $config_path"
                continue
            fi
        fi
        remove_file "$config_path"
        remove_empty_users_workspace_marker "$(dirname "$config_path")"
        remove_empty_dir "$(dirname "$config_path")"
    done <<EOF
$(config_paths)
EOF
    # Config parents that only exist because of this install (recorded by install.sh).
    while IFS= read -r created_dir; do
        if [ -n "$created_dir" ]; then
            remove_empty_dir "$created_dir"
        fi
    done <<EOF
$created_dirs
EOF
else
    log "Keeping WGS Extract config.toml."
fi

if [ "$REMOVE_PIXI_SELECTED" = "1" ]; then
    remove_pixi_shell_profile_entries
    remove_pixi_home
else
    log "Keeping Pixi."
fi

log ""
log "Uninstall complete."
