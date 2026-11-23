#!/usr/bin/env bash
set -euo pipefail
out=$1
mkdir -p "$out"
python -m pytest -q -o addopts="" -o tmp_path_retention_policy=all \
    --basetemp="$out/work" --junitxml="$out/results.xml" \
    tests/test_imputation.py -k imputes_1x_reads
python -c "import sys, xml.etree.ElementTree as ET; s = ET.parse(sys.argv[1]).find('testsuite'); assert s is not None and s.get('tests') == '1' and s.get('skipped') == '0'" "$out/results.xml"

# Compare every record (including GT, DS and GP), not the build-specific
# version in the VCF header or compression metadata.
result=$(find "$out/work" -type f -name imputed.vcf.gz)
test -n "$result"
bcftools view -H "$result" | shasum -a 256 | cut -d ' ' -f 1 > "$out/records.sha256"
