# shellcheck shell=bash

pinned_digest_matches() {
  local file=$1 expected=$2 digest
  [ -f "$file" ] || return 1
  if command -v sha256sum >/dev/null 2>&1; then
    digest=$(sha256sum < "$file") || return
  else
    digest=$(shasum -a 256 < "$file") || return
  fi
  [ "${digest%% *}" = "$expected" ]
}

fetch_pinned() {
  local url=$1 expected=$2 destination=$3 partial=$3.part status
  if pinned_digest_matches "$destination" "$expected"; then
    echo "Using verified cached ${destination##*/}."
    return 0
  fi
  if [ -e "$destination" ]; then
    echo "::warning::Discarding ${destination##*/}: cached SHA-256 mismatch."
    rm -f "$destination" || return
  fi
  echo "Fetching $url (up to five attempts)."
  curl -fsSL --retry 4 --retry-all-errors --connect-timeout 10 \
    --max-time 45 --retry-max-time 90 -o "$partial" "$url" || {
    status=$?
    rm -f "$partial"
    echo "::error::Fetch of $url failed (curl exit $status)."
    return "$status"
  }
  if ! pinned_digest_matches "$partial" "$expected"; then
    rm -f "$partial"
    echo "::error::SHA-256 mismatch for $url."
    return 1
  fi
  mv -f "$partial" "$destination"
}
