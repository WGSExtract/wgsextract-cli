#!/bin/bash
set -euo pipefail
original=$1
candidate=$2
out=$3
mkdir -p "$out"

# An exact buffer-ending newline, longer records, and an unterminated final line.
perl -e '
    my $header = "\@HD\tVN:1.6\tSO:queryname\n\@SQ\tSN:chr1\tLN:1000000\n";
    print $header, "\@CO\t", "x" x (65536 - length($header) - 5), "\n";
    for my $i (1..2) {
        print join("\t", "longpair", ($i == 1 ? 99 : 147), "chr1",
            ($i == 1 ? 1 : 100001), 60, "70000M", "=", ($i == 1 ? 100001 : 1),
            ($i == 1 ? 170000 : -170000), "A" x 70000, "I" x 70000);
        print "\n" if $i == 1;
    }
' > "$out/long-last-line.sam"

for version in original candidate; do
    binary=$original
    if [[ $version == candidate ]]; then binary=$candidate; fi
    # contract: samblaster's reference padding must accommodate these long reads.
    "$binary" --maxReadLength 70000 < "$out/long-last-line.sam" > "$out/$version.sam"
    cat "$out/long-last-line.sam" | "$binary" --maxReadLength 70000 > "$out/$version-pipe.sam"
    grep -v '^@PG' "$out/$version.sam" > "$out/$version.records"
    grep -v '^@PG' "$out/$version-pipe.sam" > "$out/$version-pipe.records"
done
expected=$(sha256sum "$out/original.records" | cut -d' ' -f1)
test "$expected" = "$(sha256sum "$out/original-pipe.records" | cut -d' ' -f1)"
test "$expected" = "$(sha256sum "$out/candidate.records" | cut -d' ' -f1)"
test "$expected" = "$(sha256sum "$out/candidate-pipe.records" | cut -d' ' -f1)"
echo "SAM reader: long records, buffer crossings, EOF and pipe parity passed"
