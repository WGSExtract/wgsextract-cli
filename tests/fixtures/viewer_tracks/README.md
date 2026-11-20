# Offline viewer tracks

Synthetic panels and coverage on contig `1` (length 3000), matching the first
contig of `../vep_rs/genome.fa`. No biological sample data.

The `.gz` files are generated from the plain files with bgzip 1.24; `.tbi`
files use `tabix -p bed`, and `.csi` files use `tabix -C -p bed`.
`coverage.bw` is generated with pyBigWig 0.3.25, a `("1", 3000)` header and
the same intervals and values as `coverage.bedgraph`.

The browser fixture preparation recreates compressed tracks with installed
bgzip/tabix and copies the bigWig. It never installs tools or downloads data.
