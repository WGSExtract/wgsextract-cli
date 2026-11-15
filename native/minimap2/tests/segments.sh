#!/bin/sh
# Index construction packs and sketches long sequences in blocks and segments on several
# threads. Build indexes of a synthetic genome with a copy that uses tiny blocks and
# segments, and require them to be byte-identical to the release build's (whose blocks and
# segments are longer than these sequences, so it sketches each sequence in one pass).
# The genome has ambiguous bases and N runs of several lengths, long homopolymer runs
# (whose warm-up is too short in HPC mode, so that sequence falls back to one pass), and
# sequences too short or too ambiguous to split.
# Usage: segments.sh <wgse-minimap2> <wgse-minimap2-segments> <work-dir>
set -eu
# shellcheck source=native/tests/seeded_random.sh
. "$(dirname "$0")/../../tests/seeded_random.sh"
release=$1
segments=$2
work=$3

rm -rf "$work"
mkdir -p "$work"
cd "$work"

awk "$seeded_random"'BEGIN {
    seed(364)
    split("A C G T", base, " ")
    contig("chr1", 400000, "random")
    contig("chr2", 120000, "homopolymer")
    contig("chr3", 3000, "gappy")
    contig("chr4", 500, "random")
    contig("chr5", 2000, "n")
    contig("chr6", 90000, "random")
}
function contig(name, size, kind,    i, j, b, line, n_gap) {
    print ">" name
    line = ""; n_gap = 0
    for (i = 1; i <= size; i++) {
        if (kind == "n") b = "N"
        else if (kind == "gappy") b = i % 800 == 0 ? "N" : base[int(rnd() * 4) + 1]
        else if (n_gap > 0) { b = "N"; n_gap-- }
        else if (kind == "homopolymer") {
            # Runs of 1200 identical bases between 24 random ones. In HPC mode each segment
            # then starts after a run, with no k-mer counted in its warm-up, while the
            # window holds valid k-mers of the bases before the run.
            j = (i - 1) % 1224
            if (j == 0) hb = base[int(rnd() * 4) + 1]
            b = j < 1200 ? hb : base[int(rnd() * 4) + 1]
        } else {
            b = base[int(rnd() * 4) + 1]
            r = rnd()
            if (r < 0.00002) n_gap = int(rnd() * 3000)
            else if (r < 0.00004) b = "R"
            else if (r < 0.0004) b = tolower(b)
        }
        line = line b
        if (length(line) == 60) { print line; line = "" }
    }
    if (line != "") print line
}' > reference.fa

for preset in map-ont map-hifi map-pb sr; do
    "$release" -t 4 -x "$preset" -d "release.$preset.mmi" reference.fa 2> "release.$preset.log"
    "$segments" -t 4 -x "$preset" -d "segments.$preset.mmi" reference.fa 2> "segments.$preset.log"
    cmp "release.$preset.mmi" "segments.$preset.mmi"
    "$segments" -t 1 -x "$preset" -d "segments1.$preset.mmi" reference.fa 2> "segments1.$preset.log"
    cmp "release.$preset.mmi" "segments1.$preset.mmi"
done
echo "segmented index construction matches for map-ont, map-hifi, map-pb and sr"
