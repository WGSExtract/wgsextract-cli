#!/usr/bin/env bash
set -euo pipefail

library="${1:-}"
final="${2:-}"
# The Reference Genomes Library, else genomes/ in the reference library.
genomes="${3:-}"
if [[ -z "$library" ]]; then
  library="${WGSEXTRACT_GUI_BUNDLE_WORKSPACE:-$PWD}/reference"
fi
if [[ -z "$final" || "$final" == */* || "$final" == "." || "$final" == ".." ]]; then
  printf 'Invalid reference genome file name: %s\n' "$final" >&2
  exit 2
fi

target_dir="${genomes:-$library/genomes}"
target="$target_dir/$final"
if [[ ! -d "$target_dir" ]]; then
  printf 'Reference genome directory does not exist: %s\n' "$target_dir" >&2
  exit 2
fi
canonical_genomes="$(cd "$target_dir" 2>/dev/null && pwd -P)"

canonical_delete_path() {
  local path="$1"
  local path_dir path_base canonical_dir canonical_path
  path_dir="$(dirname "$path")"
  path_base="$(basename "$path")"
  canonical_dir="$(cd "$path_dir" 2>/dev/null && pwd -P)" || return 1
  canonical_path="$canonical_dir/$path_base"
  case "$canonical_path" in
    "$canonical_genomes/"*) printf '%s\n' "$canonical_path" ;;
    *) return 1 ;;
  esac
}

delete_if_present() {
  local path="$1"
  local canonical_path
  if [[ ! -e "$path" && ! -L "$path" ]]; then
    return
  fi
  canonical_path="$(canonical_delete_path "$path")" || {
    printf 'Refusing to delete outside the reference library: %s\n' "$path" >&2
    exit 2
  }
  rm -f -- "$canonical_path"
  printf 'Deleted %s\n' "$canonical_path"
  deleted=true
}

deleted=false
for suffix in "" ".partial" ".fai" ".gzi" ".dict" ".amb" ".ann" ".bwt" ".pac" ".sa"; do
  delete_if_present "$target$suffix"
done

delete_if_present "${target%.*}.dict"

if [[ "$deleted" != "true" ]]; then
  printf 'No files found for %s\n' "$target" >&2
fi
