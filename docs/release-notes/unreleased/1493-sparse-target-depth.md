## Changed

- `info calculate-coverage --by` and `typing target-cnv` count uncovered target bases without asking samtools to scan across empty spans. Depth and breadth calculations are unchanged ([#1493, slow test tail](https://github.com/WGSExtract/wgsextract-cli/issues/1493)).
