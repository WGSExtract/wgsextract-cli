#!/bin/sh
set -eu
host=randy-mini
url=http://randy-mini.tail720e6b.ts.net:8765/api/health
retry_delay=10
coordinator="$HOME/.local/bin/agent-coord"
while [ "$#" -gt 0 ]; do
    if [ "$#" -lt 2 ]; then echo "Missing option value" >&2; exit 2; fi
    case "$1" in
        --host) host=$2 ;;
        --url) url=$2 ;;
        --retry-delay) retry_delay=$2 ;;
        --agent-coord) coordinator=$2 ;;
        *) echo "Unknown option: $1" >&2; exit 2 ;;
    esac
    shift 2
done
healthy() {
    curl --fail --silent --show-error --connect-timeout 10 --max-time 20 "$url" |
        grep -Eq '"ok"[[:space:]]*:[[:space:]]*true'
}
alert() {
    "$coordinator" send --to all "#776 mockup server down on $host; $1"
}
if healthy; then
    exit 0
fi
ssh_failed=0
ssh -o BatchMode=yes -o ConnectTimeout=15 "$host" '
    /usr/bin/python3 /Users/wgse/mockup-server/app/gui/mockups/hosting/recover.py
' || ssh_failed=1
sleep "$retry_delay"
if healthy; then
    alert "restarted OK"
elif [ "$ssh_failed" -eq 1 ]; then
    alert "ssh unreachable / restart failed; still down"
    exit 1
else
    alert "still down after restart"
    exit 1
fi
