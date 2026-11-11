#!/usr/bin/env bash
# Install Ubuntu packages without hanging on, or being misled by, a package
# mirror outage. Run as root on an Ubuntu host or container.
#
#   apt-install.sh [--cache-dir DIR] [--no-install-recommends] PACKAGE...
#
# - The Ubuntu archive is reached through a list of official mirrors (apt's
#   mirror+file method). Each is probed first, and only those that answer are
#   used, in order, each falling through to the next. Every mirror serves the
#   same Release files signed by Ubuntu's archive key, which apt verifies;
#   nothing here relaxes that.
# - Connections time out and are retried, and the update and the install are
#   each bounded, so an outage fails in minutes rather than hours.
# - `apt-get update` fails if any index can't be fetched, instead of warning
#   and carrying on into a misleading "Unable to locate package".
# - With --cache-dir, package lists and downloaded .debs live in DIR (for a CI
#   cache). Cached lists are used only if they match a Release file signed by
#   Ubuntu's archive key. If no mirror answers, the packages are installed from
#   DIR's lists and .debs, apt checking each .deb against those lists.
#
# Exit status 75 (EX_TEMPFAIL): no mirror could be reached. It comes with a
# GitHub "Upstream package mirror unreachable" error annotation, which
# rerun_lost_runners.yml looks for.
set -euo pipefail

OUTAGE_TITLE="Upstream package mirror unreachable"
EX_TEMPFAIL=75
KEYRING=/usr/share/keyrings/ubuntu-archive-keyring.gpg
PROBE_SECONDS=20
UPDATE_SECONDS=480
INSTALL_SECONDS=900
# Official mirrors outside Canonical's and Azure's networks (Fastly-backed
# kernel.org, MIT, Berkeley): https://launchpad.net/ubuntu/+archivemirrors
FALLBACK_MIRRORS=(
  http://mirrors.edge.kernel.org/ubuntu/
  http://mirrors.mit.edu/ubuntu/
  http://mirrors.ocf.berkeley.edu/ubuntu/
)
NETWORK_ERRORS='Could not resolve|Temporary failure resolving|Could not connect|Unable to connect|Connection failed|Connection refused|timed out|Network is unreachable|No route to host|Hash Sum mismatch|File has unexpected size|Failed to fetch'
# Never an outage: a bad or missing signature fails as itself.
SIGNATURE_ERRORS='NO_PUBKEY|BADSIG|EXPKEYSIG|signatures were invalid|signatures couldn.t be verified|is not signed'

usage() {
  echo "usage: $0 [--cache-dir DIR] [--no-install-recommends] PACKAGE..." >&2
  exit 2
}

cache_dir=
install_flags=()
while [ $# -gt 0 ]; do
  case $1 in
    --cache-dir) [ $# -ge 2 ] || usage; cache_dir=$2; shift 2 ;;
    --no-install-recommends) install_flags+=(--no-install-recommends); shift ;;
    --) shift; break ;;
    -*) usage ;;
    *) break ;;
  esac
done
[ $# -gt 0 ] || usage
[ "$(id -u)" -eq 0 ] || { echo "$0: run as root" >&2; exit 2; }

# shellcheck source=/dev/null
. /etc/os-release
[ "${ID:-}" = ubuntu ] || { echo "$0: not Ubuntu (ID=${ID:-unknown})" >&2; exit 2; }
export DEBIAN_FRONTEND=noninteractive

work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
log=$work/apt.log

# The mirrors to try, in order: the machine's own (GitHub's runners list
# Azure's first, in /etc/apt/apt-mirrors.txt), Ubuntu's, then the fallbacks.
candidates=()
case $(dpkg --print-architecture) in
  amd64 | i386)
    if [ -f /etc/apt/apt-mirrors.txt ]; then
      while read -r uri _; do
        case $uri in http://* | https://*) candidates+=("$uri") ;; esac
      done < /etc/apt/apt-mirrors.txt
    fi
    candidates+=(http://archive.ubuntu.com/ubuntu/ http://security.ubuntu.com/ubuntu/ "${FALLBACK_MIRRORS[@]}")
    ;;
  *) candidates+=(http://ports.ubuntu.com/ubuntu-ports/) ;;
esac
# https needs ca-certificates, which a bare container doesn't have yet.
if [ ! -f /etc/ssl/certs/ca-certificates.crt ]; then
  mapfile -t candidates < <(printf '%s\n' "${candidates[@]}" | grep '^http://')
fi
mapfile -t candidates < <(printf '%s\n' "${candidates[@]}" | awk '!seen[$0]++')

outage() {
  echo "::error title=$OUTAGE_TITLE::$OUTAGE_TITLE: $1 Tried ${candidates[*]}. This is an outage outside this repository, not a failure of the change under test; rerun the job later."
  exit "$EX_TEMPFAIL"
}

# apt retries every file on each mirror in turn, so one that hangs costs its
# timeouts on every file: probe them all at once and keep those that answer.
# Only reachability is tested here; apt verifies what it later fetches.
for i in "${!candidates[@]}"; do
  timeout "$PROBE_SECONDS" /usr/lib/apt/apt-helper \
    -o Acquire::http::Timeout=10 -o Acquire::https::Timeout=10 -o Acquire::Retries=0 \
    -o APT::Sandbox::User=root \
    download-file "${candidates[$i]}dists/$VERSION_CODENAME/InRelease" "$work/probe-$i" \
    > /dev/null 2>&1 &
done
wait || true
mirrors=()
for i in "${!candidates[@]}"; do
  if [ -s "$work/probe-$i" ]; then
    mirrors+=("${candidates[$i]}")
  else
    echo "Ubuntu mirror ${candidates[$i]} did not answer; skipping it."
  fi
done

mirror_list=/etc/apt/ci-ubuntu-mirrors.txt
sources="Types: deb
URIs: mirror+file:$mirror_list
Suites: $VERSION_CODENAME $VERSION_CODENAME-updates $VERSION_CODENAME-backports $VERSION_CODENAME-security
Components: main restricted universe multiverse
Targets: Packages
Signed-By: $KEYRING
"
# The machine's Ubuntu sources become the mirror list, so later apt commands
# (an installer under test, say) use the lists fetched here. This script's own
# commands read only these sources: another repository's outage is not ours to
# fail on, and its lists are left alone.
printf '%s' "$sources" > /etc/apt/sources.list.d/ubuntu.sources
if [ -f /etc/apt/sources.list ]; then
  sed -i -E 's@^(deb(-src)? .*(ubuntu\.com|apt-mirrors\.txt).*)@# \1@' /etc/apt/sources.list
fi
mkdir -p /etc/apt/ci-sources.d
printf '%s' "$sources" > /etc/apt/ci-sources.d/ubuntu.sources

apt_opts=(
  -o Acquire::Retries=2
  -o Acquire::http::Timeout=20
  -o Acquire::https::Timeout=20
  -o Acquire::Languages=none
  -o Dir::Etc::SourceList=/dev/null
  -o Dir::Etc::SourceParts=/etc/apt/ci-sources.d
  -o APT::Get::List-Cleanup=false
  -o DPkg::Lock::Timeout=120
)
# A restored cache is not trusted: apt neither re-checks the lists it already
# has on `update` (when their Release entry is unchanged) nor at all with
# --no-download. So every cached list must match the hashes in a Release file
# signed by Ubuntu's archive key, or the lists are discarded. The .debs are
# then checked by apt against these lists.
verify_cached_lists() {
  local lists=$cache_dir/lists release prefix file path expected
  local -A verified=()
  for release in "$lists"/*_InRelease; do
    [ -e "$release" ] || continue
    gpgv --keyring "$KEYRING" --output "$work/Release" "$release" 2> /dev/null || return 1
    prefix=${release%InRelease}
    verified[$release]=1
    for file in "$prefix"*; do
      [ "$file" != "$release" ] || continue
      path=${file#"$prefix"}
      path=${path%.lz4}
      path=${path%.gz}
      path=${path%.xz}
      path=${path//_//}
      # The SHA256 entry for the uncompressed index: " <hash> <size> <path>".
      expected=$(awk -v p="$path" '/^[^ ]/ { s = ($1 == "SHA256:") } s && $3 == p { print $1 }' "$work/Release")
      [ -n "$expected" ] || return 1
      [ "$(/usr/lib/apt/apt-helper cat-file "$file" | sha256sum | cut -d' ' -f1)" = "$expected" ] || return 1
      verified[$file]=1
    done
  done
  for file in "$lists"/*; do
    [ -f "$file" ] && [ "${file##*/}" != lock ] || continue
    [ -n "${verified[$file]:-}" ] || return 1
  done
}

if [ -n "$cache_dir" ]; then
  if ! verify_cached_lists; then
    echo "::warning::Cached package lists in $cache_dir failed verification; discarding them."
    rm -rf "$cache_dir/lists"
  fi
  mkdir -p "$cache_dir/lists/partial" "$cache_dir/archives/partial"
  apt_opts+=(
    -o "Dir::State::Lists=$cache_dir/lists"
    -o "Dir::Cache::Archives=$cache_dir/archives"
    -o APT::Keep-Downloaded-Packages=true
  )
fi

# apt-get within a time limit, its output shown and kept in $log. Returns its
# status (124: timed out).
apt_get() {
  local seconds=$1
  shift
  local status=0
  timeout --kill-after=30 "$seconds" apt-get "${apt_opts[@]}" "$@" 2>&1 | tee "$log" || status=${PIPESTATUS[0]}
  return "$status"
}

network_failure() {
  ! grep -Eq "$SIGNATURE_ERRORS" "$log" &&
    { [ "$1" -eq 124 ] || [ "$1" -eq 137 ] || grep -Eq "$NETWORK_ERRORS" "$log"; }
}

install_from_cache() {
  [ -n "$cache_dir" ] && compgen -G "$cache_dir/lists/*_Packages*" > /dev/null || return 1
  echo "::warning title=$OUTAGE_TITLE::$OUTAGE_TITLE: no Ubuntu mirror answered; installing $* from the cached package lists and .debs."
  apt_get "$INSTALL_SECONDS" install -y --no-download "${install_flags[@]}" "$@"
}

status=0
if [ ${#mirrors[@]} -eq 0 ]; then
  : > "$mirror_list"
  install_from_cache "$@" && exit 0
  outage "No Ubuntu mirror answered."
fi
# Without priorities, apt would try them in a random order.
for i in "${!mirrors[@]}"; do
  printf '%s\tpriority:%d\n' "${mirrors[$i]}" "$((i + 1))"
done > "$mirror_list"
echo "Ubuntu mirrors, in order: ${mirrors[*]}"

apt_get "$UPDATE_SECONDS" update --error-on=any || status=$?
if [ "$status" -ne 0 ]; then
  network_failure "$status" || exit "$status"
  install_from_cache "$@" && exit 0
  outage "apt-get update could not fetch the Ubuntu package lists."
fi

apt_get "$INSTALL_SECONDS" install -y "${install_flags[@]}" "$@" || status=$?
if [ "$status" -ne 0 ]; then
  if network_failure "$status"; then outage "apt-get install could not download $*."; fi
  exit "$status"
fi
# Drop cached .debs these lists no longer offer.
if [ -n "$cache_dir" ]; then apt-get "${apt_opts[@]}" autoclean -qq; fi
