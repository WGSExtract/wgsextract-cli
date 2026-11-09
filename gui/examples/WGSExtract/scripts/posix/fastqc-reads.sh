#!/bin/sh
# FastQC on FASTQ R1 and, when given, R2; with --multiqc and both reads, one MultiQC report merging the two.
set -eu

if [ "$#" -lt 1 ]; then
  printf 'Usage: %s FASTQ_R1 [--r2 FASTQ_R2] [--outdir DIR] [--multiqc]\n' "$0" >&2
  exit 2
fi

r1="$1"
shift
r2=""
out_dir=""
multiqc=0
while [ "$#" -gt 0 ]; do
  case "$1" in
    --r2) r2="$2"; shift 2 ;;
    --outdir) out_dir="$2"; shift 2 ;;
    --multiqc) multiqc=1; shift ;;
    *) printf 'Unknown argument: %s\n' "$1" >&2; exit 2 ;;
  esac
done
script_dir="$(CDPATH= cd "$(dirname "$0")" && pwd)"
if [ -z "$out_dir" ]; then
  out_dir="$(dirname "$r1")"
fi

sh "$script_dir/run-wgsextract.sh" qc fastqc --input "$r1" --outdir "$out_dir"
if [ -n "$r2" ]; then
  sh "$script_dir/run-wgsextract.sh" qc fastqc --input "$r2" --outdir "$out_dir"
  if [ "$multiqc" -eq 1 ]; then
    sh "$script_dir/run-wgsextract.sh" qc multiqc --input "$out_dir" --outdir "$out_dir" --force
  fi
fi
