#!/bin/sh
set -eu

# The settings file and the folders the user manages live in the users workspace
# (~/wgs-extract); what only the app needs (run logs) in its hidden bundle workspace.
bundle="${WGSEXTRACT_GUI_BUNDLE_WORKSPACE:-$(pwd)}"
users="${WGSEXTRACT_GUI_USERS_WORKSPACE:-$bundle}"
config_path="${WGSEXTRACT_GUI_CONFIG_PATH:-$users/config.toml}"
# bundled: the app installed the CLI, so its large system folders (reference data,
# reference genomes, temporary files) go in the users workspace too, where the user
# can see and move them. separate: a CLI installed on its own (on a server, say)
# keeps them where it does by default, local to it, so they are left unset.
cli_install="${WGSEXTRACT_GUI_CLI_INSTALL:-bundled}"

# An existing config keeps every folder it names; only missing settings are added.
existing_value() {
  [ -f "$config_path" ] || return 0
  sed -n "s/^$1 = \"\(.*\)\"\r\{0,1\}$/\1/p" "$config_path" | head -n 1 | sed 's/\\\\/\\/g; s/\\"/"/g'
}
has_files() {
  [ -d "$1" ] && [ -n "$(ls -A "$1" 2>/dev/null)" ]
}

# The My Genomes library, under any of its older names. One that is a workspace's
# datasets/ folder is renamed mygenomes/, where it stays; an empty genomes/ one
# (the oldest default) is dropped.
old_mygenomes="$(existing_value mygenomes_library)"
[ -n "$old_mygenomes" ] || old_mygenomes="$(existing_value dataset_library)"
[ -n "$old_mygenomes" ] || old_mygenomes="$(existing_value genome_library)"
mygenomes_path="$users/mygenomes"
if [ -n "$old_mygenomes" ]; then
  old_mygenomes="${old_mygenomes%/}"
  case "$old_mygenomes" in
    "$users/datasets" | "$bundle/datasets")
      renamed="$(dirname "$old_mygenomes")/mygenomes"
      if [ -d "$old_mygenomes" ] && [ ! -e "$renamed" ]; then
        mv "$old_mygenomes" "$renamed" || renamed="$old_mygenomes"
      fi
      mygenomes_path="$renamed"
      ;;
    "$users/genomes" | "$bundle/genomes")
      if has_files "$old_mygenomes"; then
        mygenomes_path="$old_mygenomes"
      fi
      ;;
    *)
      mygenomes_path="$old_mygenomes"
      ;;
  esac
fi

old_reference_library="$(existing_value reference_library)"
reference_genome_library_path="$users/genomes"
if [ -n "$old_reference_library" ] && has_files "$old_reference_library/genomes"; then
  reference_genome_library_path="$old_reference_library/genomes"
elif [ "$mygenomes_path" = "$users/genomes" ]; then
  # genomes/ already holds the user's genomes.
  reference_genome_library_path="${old_reference_library:-$users/reference}/genomes"
fi

json_escape() {
  printf '%s' "$1" | sed 's/\\/\\\\/g; s/"/\\"/g'
}
# One TOML setting, as a JSON string's contents: key = "value"\n
setting() {
  printf '%s = \\"%s\\"\\n' "$1" "$(json_escape "$(json_escape "$2")")"
}
# A folder setting the config doesn't have yet. The CLI requires its temporary and
# log folders to exist already.
folder_setting() {
  if [ -z "$(existing_value "$1")" ]; then
    case "$1" in temp_directory | log_dir) mkdir -p "$2" ;; esac
  fi
  setting "$1" "$2"
}

# Unset: each command writes to a wgse/ folder beside its input files from the lab,
# whose own folder stays untouched. Only Advanced mode may point it elsewhere.
contents="$(setting output_directory "")"
contents="$contents$(folder_setting mygenomes_library "$mygenomes_path")"
if [ "$cli_install" = "bundled" ]; then
  contents="$contents$(folder_setting reference_library "$users/reference")"
  contents="$contents$(folder_setting reference_genome_library "$reference_genome_library_path")"
  contents="$contents$(folder_setting temp_directory "$users/temp")"
fi
contents="$contents$(folder_setting log_dir "$bundle/logs")"
for key in reference_fasta default_input_vcf mother_vcf_path father_vcf_path haplogrep_executable; do
  contents="$contents$(setting "$key" "")"
done

printf '{\n'
printf '  "path": "%s",\n' "$(json_escape "$config_path")"
printf '  "contents": "%s"\n' "$contents"
printf '}\n'
