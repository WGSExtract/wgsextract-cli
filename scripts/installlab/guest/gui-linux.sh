#!/bin/sh
# shellcheck disable=SC1007
# Install-lab guest scenario for the WGS Extract Linux app (Tauri, .deb).
#
# baseline snapshot -> download the .deb -> `sudo apt install` it -> the app
# runs its own setup on a virtual display -> real actions through the app's
# bundled runtime -> snapshot -> `sudo apt remove` -> snapshot
#
# Inputs (environment), besides LAB_RESULTS / LAB_NONCE / LAB_MODE (see scenario.sh):
#   LAB_GUI_INSTALLER_URL / LAB_GUI_INSTALLER_NAME   the .deb under test
#   LAB_CLI_ARCHIVE_URL   optional CLI source archive for setup to install
#                         (unset: setup installs the CLI release the app pins)
# The user is an Ubuntu desktop user with sudo (as Ubuntu's first user is);
# Xvfb stands in for their display. Nothing here decides pass/fail.
set -u

R="${LAB_RESULTS:?}"
HERE="$(CDPATH= cd "$(dirname "$0")" && pwd)"
EVIDENCE="$R/evidence"
mkdir -p "$EVIDENCE" "$R/workload"
printf '%s\n' "${LAB_NONCE:?}" > "$R/nonce"
MODE="${LAB_MODE:-install}"
DEB="$HOME/Downloads/${LAB_GUI_INSTALLER_NAME:-WGSExtract.deb}"
PACKAGE="wgs-extract"
APP_DIR="/usr/lib/WGSExtract"
APP_BIN="/usr/bin/wgsextract-gui"
# Released apps up to 0.8.6 keep their data under this identifier and read
# GUI_FOR_CLI_* variables; newer ones read WGSEXTRACT_GUI_* and keep their data,
# with the CLI runtime, in APP_DATA. The lab passes both.
APP_ID="dev.guiforcli.web.embed.wgsextract"
APP_DATA="$HOME/.local/share/WGSExtract-gui"
# Apps since the users workspace split keep the bundle workspace in APP_DATA and
# the user's settings and folders in USERS_WORKSPACE; 0.8.5 to 0.8.7 kept both in
# the home folder (WORKSPACE); released apps before them in their app data
# (OLD_WORKSPACE). app_actions uses the
# one setup made.
USERS_WORKSPACE="$HOME/wgs-extract"
WORKSPACE="$USERS_WORKSPACE"
USERS_ARGS=""
OLD_WORKSPACE="$HOME/.local/share/$APP_ID/BundleWorkspaces/wgs-extract"
# A desktop session's runtime folder (logind's /run/user/<uid>), where dconf
# keeps its runtime state; without one it falls back to ~/.cache/dconf.
XDG_RUNTIME_DIR="/tmp/runtime-$(id -u)"
export XDG_RUNTIME_DIR
mkdir -p "$XDG_RUNTIME_DIR" && chmod 700 "$XDG_RUNTIME_DIR"

# The app's main process (its xvfb-run is app_pid), and everything it starts:
# its backend, and setup's scripts and downloads, which run from the workspace.
APP_MAIN="^$APP_BIN"
APP_TREE="$APP_DIR/|$APP_BIN|$APP_DATA/|$WORKSPACE/|$OLD_WORKSPACE/"
# shellcheck source=scripts/installlab/guest/app-lifecycle.sh
. "$HERE/app-lifecycle.sh"

step() {
    name="$1"
    shift
    start=$(date +%s)
    mark "step-start $name"
    "$@" > "$EVIDENCE/$name.log" 2>&1
    rc=$?
    mark "step-end $name $rc"
    echo "$rc" > "$EVIDENCE/$name.rc"
    echo $(( $(date +%s) - start )) > "$EVIDENCE/$name.seconds"
    echo "scenario step $name rc=$rc"
}

snapshot() {
    sh "$HERE/inventory.sh" "$R/inventory-$1.txt" "$R"
}

# The user's own `sudo apt-get`. The lab's machine setup (apt-install.sh)
# already pointed the Ubuntu sources at the mirrors that answered, in fallback
# order, and fetched their lists; this only adds time limits, and names a
# mirror outage as one rather than as a failure of the package under test.
APT_OUTAGE="$R/apt-outage.txt"
user_apt() {
    apt_log="$(mktemp)"
    # The log is the user's own file; only apt-get needs root.
    # shellcheck disable=SC2024
    sudo DEBIAN_FRONTEND=noninteractive timeout --kill-after=30 900 apt-get \
        -o Acquire::Retries=3 -o Acquire::http::Timeout=30 -o Acquire::https::Timeout=30 \
        "$@" > "$apt_log" 2>&1
    apt_rc=$?
    cat "$apt_log"
    if [ "$apt_rc" != 0 ] && ! grep -Eq 'NO_PUBKEY|BADSIG|EXPKEYSIG|signatures' "$apt_log" &&
        { [ "$apt_rc" = 124 ] || [ "$apt_rc" = 137 ] ||
            grep -Eq 'Could not resolve|Temporary failure resolving|Could not connect|Unable to connect|Connection failed|timed out|Network is unreachable|No route to host|Failed to fetch' "$apt_log"; }; then
        echo "apt-get $1 could not reach the Ubuntu mirrors ($(cut -f1 /etc/apt/ci-ubuntu-mirrors.txt 2>/dev/null | paste -sd ' ' -))." |
            tee -a "$APT_OUTAGE"
    fi
    rm -f "$apt_log"
    return "$apt_rc"
}

# A GitHub error annotation for a mirror outage seen by the step before.
report_apt_outage() {
    if [ -s "$APT_OUTAGE" ]; then
        echo "::error title=Upstream package mirror unreachable::Upstream package mirror unreachable: $(tr '\n' ' ' < "$APT_OUTAGE")This is an outage outside this repository, not a failure of the app under test; rerun the job later."
        rm -f "$APT_OUTAGE"
    fi
}

download_installer() {
    mkdir -p "$HOME/Downloads" && curl -fsSL -o "$DEB" "$LAB_GUI_INSTALLER_URL"
}

install_app() {
    user_apt install -y -q "$DEB" &&
        dpkg -s "$PACKAGE" && test -x "$APP_BIN" &&
        test -f /usr/share/applications/WGSExtract.desktop
}

app_setup() {
    events="$R/setup-events.ndjson"
    # Opening the app; it runs its own setup.
    env GUI_FOR_CLI_AUTO_RUN_SETUP=1 GUI_FOR_CLI_SETUP_EVENTS_LOG="$events" \
        WGSEXTRACT_GUI_AUTO_RUN_SETUP=1 WGSEXTRACT_GUI_SETUP_EVENTS_LOG="$events" \
        ${LAB_CLI_ARCHIVE_URL:+WGSEXTRACT_ARCHIVE_URL="$LAB_CLI_ARCHIVE_URL"} \
        xvfb-run -a -s "-screen 0 1280x800x24" "$APP_BIN" > "$R/app-stdout.txt" 2> "$R/app-stderr.txt" &
    app_pid=$!
    app_launched
    await_setup 10800 || return 1
    python3 - "$events" "$R/setup-result.json" <<'EOF'
import json, sys
results = [json.loads(line) for line in open(sys.argv[1]) if line.strip()]
complete = [e for e in results if e.get("type") == "complete"][-1]
json.dump(complete["result"], open(sys.argv[2], "w"), indent=2)
print("setup:", complete["result"]["status"])
EOF
    close_app "setup complete"
}

app_actions() {
    if [ ! -e "$USERS_WORKSPACE/.bundle-workspace" ] && [ -e "$APP_DATA/BundleWorkspaces/wgs-extract/.bundle-workspace" ]; then
        WORKSPACE="$APP_DATA/BundleWorkspaces/wgs-extract"
        USERS_ARGS="--users-workspace"
    elif [ ! -e "$USERS_WORKSPACE/.bundle-workspace" ] && [ -e "$OLD_WORKSPACE/.bundle-workspace" ]; then
        WORKSPACE="$OLD_WORKSPACE"
    fi
    runner="$APP_DIR/app/dist/web/src/server/bundle-test-cli.js"
    # Released apps before the single-app layout use platform/typescript.
    if [ ! -f "$runner" ]; then
        runner="$APP_DIR/platform/typescript/dist/web/src/server/bundle-test-cli.js"
    fi
    GUI_FOR_CLI_APP_SUPPORT_NAME="$APP_ID" "$APP_DIR/node/bin/node" \
        "$runner" \
        --plan "$HERE/gui-plan.json" --workspace "$WORKSPACE" ${USERS_ARGS:+"$USERS_ARGS" "$USERS_WORKSPACE"} \
        --report "$R/bundle-report.json" --log "$R/bundle-test.log" \
        "$APP_DIR/examples/EmbeddedBundle"
    rc=$?
    if [ -d "$WORKSPACE/lab-output" ]; then
        cp -R "$WORKSPACE/lab-output/." "$R/workload/"
    fi
    mkdir -p "$R/workload/info"
    find "$USERS_WORKSPACE/output" "$USERS_WORKSPACE/mygenomes" "$WORKSPACE/output" "$WORKSPACE/datasets" "$WORKSPACE/genomes" -name '*.wgse_info.json' \
        -exec cp {} "$R/workload/info/" \; 2>/dev/null
    return $rc
}

app_uninstall() {
    # The app's own Uninstall (File > Uninstall WGSExtract...; the package
    # manager can't remove what setup put in the home folder), which quits the app...
    env GUI_FOR_CLI_AUTO_RUN_UNINSTALL=1 GUI_FOR_CLI_UNINSTALL_RESULT_FILE="$R/uninstall-result.json" \
        WGSEXTRACT_GUI_AUTO_RUN_UNINSTALL=1 WGSEXTRACT_GUI_UNINSTALL_RESULT_FILE="$R/uninstall-result.json" \
        xvfb-run -a -s "-screen 0 1280x800x24" "$APP_BIN" > "$R/app-uninstall-stdout.txt" 2>&1
    rc=$?
    cat "$R/uninstall-result.json"
    [ "$rc" = 0 ] || { echo "the app's Uninstall exited $rc"; return 1; }
    # ...then removing the package, and what apt installed only for it.
    user_apt remove --autoremove -y -q "$PACKAGE" &&
        ! dpkg -s "$PACKAGE" 2>/dev/null
}

# A desktop user's home has the XDG folders their session made.
mkdir -p "$HOME/Downloads" "$HOME/.local/share" "$HOME/.cache" "$HOME/.config"
snapshot baseline
for tool in pixi samtools bcftools java conda mamba; do
    printf '%s\t%s\n' "$tool" "$(command -v "$tool" 2>/dev/null)"
done > "$R/preexisting-tools.txt"
# Installers reuse a Pixi in its default place even when PATH doesn't reach it.
pixi_home="$HOME/.pixi/bin/pixi"
[ -e "$pixi_home" ] || pixi_home=""
printf 'pixi-home\t%s\n' "$pixi_home" >> "$R/preexisting-tools.txt"
if [ "$MODE" = "control" ]; then
    # A different WebKitGTK app (webkit-window.py) is installed, open for 4
    # minutes and removed: what GTK, WebKit and Mesa keep for any such app.
    set -- gir1.2-webkit2-4.1 python3-gi
    user_apt install -y -q "$@" > "$R/control-apt.log" 2>&1
    report_apt_outage
    xvfb-run -a -s "-screen 0 1280x800x24" python3 "$HERE/webkit-window.py" 240 > "$R/control-window.log" 2>&1
    user_apt remove --autoremove -y -q "$@" >> "$R/control-apt.log" 2>&1
    snapshot after_install
    snapshot after_uninstall
    echo "scenario finished"
    exit 0
fi

step download_installer download_installer
step install_app install_app
report_apt_outage
step setup app_setup
step optional_tools sh "$HERE/install-optional-tools.sh" "$APP_DATA" "$WORKSPACE" "$HOME/.local/share/$APP_ID"
step actions app_actions
snapshot after_install
step uninstall app_uninstall
report_apt_outage
rm -f "$DEB"
snapshot after_uninstall
echo "scenario finished"
