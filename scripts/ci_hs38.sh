#!/usr/bin/env bash
set -euo pipefail

download_verified() {
  local url=$1 sha256=$2 file=$3
  echo "Fetching pinned reference $url (up to four attempts)."
  if ! curl --fail --silent --show-error --location --retry 3 --retry-all-errors \
    --connect-timeout 15 --max-time 600 --retry-max-time 900 --remove-on-error --output "$file" "$url"; then
    echo "::error::Could not fetch pinned reference $url." >&2
    return 1
  fi
  if ! printf '%s  %s\n' "$sha256" "$file" | shasum -a 256 -c; then
    rm -f "$file"
    return 1
  fi
}

build_indexes() {
  local stage=$1
  pixi run --no-install samtools dict -u hs38.fa.gz "$stage/hs38.fa.gz" -o "$stage/hs38.dict"
  pixi run --no-install samtools faidx "$stage/hs38.fa.gz"
  tar --mtime=@0 --owner=0 --group=0 --numeric-owner --mode=0644 \
    -C "$stage" -cf - hs38.dict hs38.fa.gz.fai hs38.fa.gz.gzi |
    gzip -n > "$stage/hs38-indexes.tar.gz"
}

install_reference() {
  local stage=$1 genomes=$2 fasta_url=$3 fasta_sha256=$4 indexes_url=$5 indexes_sha256=$6
  mkdir -p "$stage"
  download_verified "$fasta_url" "$fasta_sha256" "$stage/hs38.fa.gz"
  download_verified "$indexes_url" "$indexes_sha256" "$stage/hs38-indexes.tar.gz"
  tar -xzf "$stage/hs38-indexes.tar.gz" -C "$stage" hs38.dict hs38.fa.gz.fai hs38.fa.gz.gzi
  mkdir -p "$genomes"
  mv "$stage"/hs38.{dict,fa.gz,fa.gz.fai,fa.gz.gzi} "$genomes/"
}

main() {
  local releases=https://github.com/WGSExtract/wgsextract-cli/releases/download
  local stage=out/ci-hs38
  local fasta_url=$releases/v0.1.0/hs38.fa.gz
  local fasta_sha256=3006daf69d79db2ec6796937975015986f2114d1da145e5a838304945fe34d57
  case ${1-} in
    build)
      mkdir -p "$stage"
      download_verified "$fasta_url" "$fasta_sha256" "$stage/hs38.fa.gz"
      build_indexes "$stage"
      ;;
    install)
      install_reference "$stage" reference/genomes "$fasta_url" "$fasta_sha256" \
        "$releases/ci-hs38-1/hs38-indexes.tar.gz" 97c192453124d26b6f458db7256ab6e85bf50584441ca18d749f306735cbef79
      ;;
    *)
      echo "Usage: $0 {build|install}" >&2
      return 2
      ;;
  esac
}

if [[ ${BASH_SOURCE[0]} == "$0" ]]; then
  main "$@"
fi
