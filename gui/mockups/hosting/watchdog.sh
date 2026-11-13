#!/bin/sh
set -eu
host=randy-mini
url=http://randy-mini.tail720e6b.ts.net:8765/api/health
if curl --fail --silent --show-error --connect-timeout 10 --max-time 20 "$url" |
    grep -Eq '"ok"[[:space:]]*:[[:space:]]*true'; then
    exit 0
fi
ssh -o BatchMode=yes -o ConnectTimeout=15 "$host" '
    /bin/launchctl bootstrap user/502 /Users/wgse/Library/LaunchAgents/org.wgsextract.mockups.plist 2>/dev/null ||
    /bin/launchctl kickstart -k user/502/org.wgsextract.mockups
'
agent-coord send --to all "#776 mockup server down on randy-mini; restarted"
sleep 10
curl --fail --silent --show-error --connect-timeout 10 --max-time 20 "$url" |
    grep -Eq '"ok"[[:space:]]*:[[:space:]]*true'
