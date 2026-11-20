#!/usr/bin/env bash
set -euxo pipefail

destination="$PREFIX/share/wgse-clair3"
mkdir -p "$destination" "$PREFIX/bin"
cp -R clair3 preprocess postprocess scripts shared "$destination/"
cp finalize.py "$destination/scripts/wgse_finalize.py"
cp clair3.py run_clair3.py LICENSE.md "$destination/"
cp LICENSE "$destination/WGSE-LICENSE"
install -m 755 wgse-clair3 "$PREFIX/bin/wgse-clair3"
# Conda relocates this prefix so the launcher and workers share pinned Python.
sed -i.bak "1c\\
#!$PREFIX/bin/python
" "$PREFIX/bin/wgse-clair3"
rm "$PREFIX/bin/wgse-clair3.bak"
# These remain owned by the pinned Bioconda dependency. Do not copy its models
# (or its compiled code) into this package, and do not overwrite its files.
ln -s ../../bin/libclair3.so "$destination/libclair3.so"
ln -s ../../bin/libclair3.cpython-311-darwin.so "$destination/libclair3.cpython-311-darwin.so"
ln -s ../../bin/longphase "$destination/longphase"
