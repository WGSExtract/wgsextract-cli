#!/bin/sh
set -eu
host=randy-mini
url=http://randy-mini.tail720e6b.ts.net:8765/api/health
retry_delay=10
coordinator="$HOME/.local/bin/agent-coord"
state_file="${XDG_STATE_HOME:-$HOME/.local/state}/mockup-server/watchdog.state"
reminder_interval=14400
now=
while [ "$#" -gt 0 ]; do
    if [ "$#" -lt 2 ]; then echo "Missing option value" >&2; exit 2; fi
    case "$1" in
        --host) host=$2 ;;
        --url) url=$2 ;;
        --retry-delay) retry_delay=$2 ;;
        --agent-coord) coordinator=$2 ;;
        --state-file) state_file=$2 ;;
        --reminder-interval) reminder_interval=$2 ;;
        --now) now=$2 ;;
        *) echo "Unknown option: $1" >&2; exit 2 ;;
    esac
    shift 2
done
if [ -z "$state_file" ] || [ -d "$state_file" ]; then
    echo "State file must name a file, not a directory" >&2
    exit 2
fi
is_seconds() {
    case "$1" in
        ''|*[!0-9]*|0[0-9]*) return 1 ;;
    esac
    [ "$1" -ge 0 ] 2>/dev/null
}
if ! is_seconds "$reminder_interval" || [ "$reminder_interval" -eq 0 ]; then
    echo "Reminder interval must be a positive integer (seconds)" >&2
    exit 2
fi
if [ -z "$now" ]; then
    now=$(date +%s)
fi
if ! is_seconds "$now"; then
    echo "Clock must give a nonnegative integer (Unix seconds)" >&2
    exit 2
fi
previous=
last_alert=0
if [ -f "$state_file" ]; then
    read -r previous last_alert < "$state_file" || previous=
    if ! is_seconds "$last_alert"; then
        previous=
        last_alert=0
    fi
fi
save_state() {
    mkdir -p "$(dirname "$state_file")"
    state_tmp="$state_file.$$.new"
    (umask 077; set -C; printf '%s %s\n' "$1" "$now" > "$state_tmp")
    trap 'rm -f "$state_tmp"' 0
    mv -f "$state_tmp" "$state_file"
}
healthy() {
    curl --fail --silent --show-error --connect-timeout 10 --max-time 20 "$url" |
        grep -Eq '"ok"[[:space:]]*:[[:space:]]*true'
}
alert() {
    "$coordinator" send --to all "#776 mockup server $1 on $host$2"
}
if healthy; then
    case "$previous" in
        ssh_failed|restart_failed) alert "recovered" "" ;;
    esac
    if [ "$previous" != up ]; then
        save_state up
    fi
    exit 0
fi
ssh_failed=0
ssh -o BatchMode=yes -o ConnectTimeout=15 "$host" '
    /usr/bin/python3 /Users/wgse/mockup-server/app/gui/mockups/hosting/recover.py
' || ssh_failed=1
sleep "$retry_delay"
if healthy; then
    if [ "$previous" != restarted ]; then
        alert "recovered" "; restarted OK"
        save_state restarted
    fi
    exit 0
elif [ "$ssh_failed" -eq 1 ]; then
    status=ssh_failed
    reason="ssh unreachable / restart failed; still down"
else
    status=restart_failed
    reason="still down after restart"
fi
if [ "$status" != "$previous" ]; then
    alert "down" "; $reason"
    save_state "$status"
elif [ "$now" -lt "$last_alert" ]; then
    # Re-arm the reminder if the wall clock moved backwards.
    save_state "$status"
elif [ "$((now - last_alert))" -ge "$reminder_interval" ]; then
    alert "down" "; $reason (reminder)"
    save_state "$status"
fi
exit 1
