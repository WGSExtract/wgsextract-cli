#!/bin/sh
set -eu

workspace="${WGSEXTRACT_GUI_BUNDLE_WORKSPACE:-$(pwd)}"
config_path="${WGSEXTRACT_GUI_CONFIG_PATH:-$workspace/config.toml}"
output_path="$workspace/output"
reference_path="$workspace/reference"
# New installs keep datasets in datasets/ and reference genomes in genomes/.
# An existing config keeps folders that hold files: its old genome_library stays
# the dataset library, and genomes already in <reference_library>/genomes stay there.
existing_value() {
  [ -f "$config_path" ] || return 0
  sed -n "s/^$1 = \"\(.*\)\"\r\{0,1\}$/\1/p" "$config_path" | head -n 1 | sed 's/\\\\/\\/g; s/\\"/"/g'
}
has_files() {
  [ -d "$1" ] && [ -n "$(ls -A "$1" 2>/dev/null)" ]
}
old_genome_library="$(existing_value genome_library)"
old_reference_library="$(existing_value reference_library)"
dataset_library_path="$workspace/datasets"
if [ -n "$old_genome_library" ]; then
  if [ "${old_genome_library%/}" != "$workspace/genomes" ] || has_files "$old_genome_library"; then
    dataset_library_path="$old_genome_library"
  fi
fi
reference_genome_library_path="$workspace/genomes"
if [ -n "$old_reference_library" ] && has_files "$old_reference_library/genomes"; then
  reference_genome_library_path="$old_reference_library/genomes"
elif [ "${dataset_library_path%/}" = "$workspace/genomes" ]; then
  # genomes/ already holds datasets.
  reference_genome_library_path="${old_reference_library:-$workspace/reference}/genomes"
fi
temp_path="$workspace/temp"
log_path="$workspace/logs"
# The CLI requires its temporary directory to exist already.
mkdir -p "$temp_path" "$log_path"

json_escape() {
  printf '%s' "$1" | sed 's/\\/\\\\/g; s/"/\\"/g'
}

# A path as a TOML basic string's contents, escaped again for the JSON around it.
toml_json() {
  json_escape "$(json_escape "$1")"
}

printf '{\n'
printf '  "path": "%s",\n' "$(json_escape "$config_path")"
printf '  "contents": "output_directory = \\"%s\\"\\nreference_library = \\"%s\\"\\nreference_genome_library = \\"%s\\"\\ndataset_library = \\"%s\\"\\ntemp_directory = \\"%s\\"\\nlog_dir = \\"%s\\"\\nreference_fasta = \\"\\"\\ndefault_input_vcf = \\"\\"\\nmother_vcf_path = \\"\\"\\nfather_vcf_path = \\"\\"\\nhaplogrep_executable = \\"\\"\\n"\n' \
  "$(toml_json "$output_path")" \
  "$(toml_json "$reference_path")" \
  "$(toml_json "$reference_genome_library_path")" \
  "$(toml_json "$dataset_library_path")" \
  "$(toml_json "$temp_path")" \
  "$(toml_json "$log_path")"
printf '}\n'
