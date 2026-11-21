# shellcheck shell=sh
# The guest user's own `sudo apt-get`, sourced by gui-linux.sh. The lab's
# machine setup (apt-install.sh) already pointed the Ubuntu sources at the
# mirrors that answered, in fallback order, and fetched their lists; this only
# adds time limits, and names a mirror outage as one rather than as a failure
# of the package under test.
#
# Needs APT_OUTAGE: the file an outage is noted in until report_apt_outage.

# user_apt ARGS...: apt-get ARGS as root, its output on stdout, its status
# returned.
user_apt() {
    apt_log="$(mktemp)"
    # The log is the user's own file; only apt-get needs root.
    # shellcheck disable=SC2024
    sudo DEBIAN_FRONTEND=noninteractive timeout --kill-after=30 900 apt-get \
        -o Acquire::Retries=3 -o Acquire::http::Timeout=30 -o Acquire::https::Timeout=30 \
        "$@" > "$apt_log" 2>&1
    apt_rc=$?
    cat "$apt_log"
    # Only apt's own network errors count: hitting the time limit alone may be
    # a hung maintainer script, which is the package's failure, not a mirror's.
    if [ "$apt_rc" != 0 ] && ! grep -Eq 'NO_PUBKEY|BADSIG|EXPKEYSIG|signatures' "$apt_log" &&
        grep -Eq 'Could not resolve|Temporary failure resolving|Could not connect|Unable to connect|Connection failed|timed out|Network is unreachable|No route to host|Failed to fetch' "$apt_log"; then
        echo "apt-get $1 could not reach the Ubuntu mirrors ($(cut -f1 /etc/apt/ci-ubuntu-mirrors.txt 2>/dev/null | paste -sd ' ' -))." |
            tee -a "${APT_OUTAGE:?}"
    fi
    rm -f "$apt_log"
    return "$apt_rc"
}

# A GitHub error annotation for a mirror outage noted since the last report.
report_apt_outage() {
    if [ -s "${APT_OUTAGE:?}" ]; then
        echo "::error title=Upstream package mirror unreachable::Upstream package mirror unreachable: $(tr '\n' ' ' < "$APT_OUTAGE")This is an outage outside this repository, not a failure of the app under test; rerun the job later."
        rm -f "$APT_OUTAGE"
    fi
}
