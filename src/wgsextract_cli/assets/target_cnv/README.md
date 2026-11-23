# Target copy-number baseline and masks

Source: [NIST Genome in a Bottle v3.5](https://ftp-trace.ncbi.nlm.nih.gov/ReferenceSamples/giab/release/genome-stratifications/v3.5/).
The GRCh37, GRCh38 and CHM13 tracks are the `segdups` and
`lowmappabilityall` BEDs in each assembly's `SegmentalDuplications` and
`Mappability` folders. The exact URLs and compressed SHA-256 digests are
in `commands/genotyping/target_cnv_data.py`.

Run `pixi run python scripts/build_target_cnv_baseline.py TRACKS src/wgsextract_cli/assets/target_cnv`
from the repository root with the six original BEDs in `TRACKS`. The
script checks their digests and uses the primary chromosome lengths from
the bundled RefSeq `panel bed` annotations. It rounds intervals outward
to 1 kb (clipped at chromosome ends), unions both masks and compresses the
result. The compact mask may flag some nearby unmapped-by-mask bases;
it never removes an upstream interval on a primary chromosome.

The script samples one seeded 1 kb baseline window from each eligible
tenth of each autosome, excluding any overlap with the bundled mask.
The result is 220 windows for GRCh37 and GRCh38, and 214 for CHM13v2.0
(six heavily masked bins have no eligible window). Baseline depth
and GC fractions are measured from each sample and its reference at run
time; they are not estimates taken from a benchmark sample.
