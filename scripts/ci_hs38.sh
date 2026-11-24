#!/usr/bin/env bash
set -euo pipefail

download_verified() {
  local url=$1 sha256=$2 file=$3
  curl --fail --silent --show-error --location --retry 3 --output "$file" "$url"
  printf '%s  %s\n' "$sha256" "$file" | shasum -a 256 -c
}

build_indexes() {
  local stage=$1
  pixi run --no-install samtools dict -u hs38.fa.gz "$stage/hs38.fa.gz" -o "$stage/hs38.dict"
  pixi run --no-install samtools faidx "$stage/hs38.fa.gz"
  tar --mtime=@0 --owner=0 --group=0 --numeric-owner --mode=0644 \
    -C "$stage" -cf - hs38.dict hs38.fa.gz.fai hs38.fa.gz.gzi |
    gzip -n > "$stage/hs38-indexes.tar.gz"
}

main() {
  local releases=https://github.com/WGSExtract/wgsextract-cli/releases/download
  local stage=out/ci-hs38
  mkdir -p "$stage"
  download_verified "$releases/v0.1.0/hs38.fa.gz" \
    3006daf69d79db2ec6796937975015986f2114d1da145e5a838304945fe34d57 "$stage/hs38.fa.gz"
  build_indexes "$stage"
}

if [[ ${BASH_SOURCE[0]} == "$0" ]]; then
  main "$@"
fi
