#!/usr/bin/env bash
# Rebuild the region-sized 1000 Genomes data tests/test_remote_benchmark.py reads
# (release remote-test-data-N) from the original EBI files, into OUT (default
# out/remote-test-data). EBI is the source; the test reads the GitHub copies,
# because EBI's servers kept failing CI. Needs samtools, bgzip, bcftools, tabix.
set -euo pipefail
out=${1:-out/remote-test-data}
mkdir -p "$out" && cd "$out"
kg=https://ftp.1000genomes.ebi.ac.uk/vol1/ftp
grch38=$kg/technical/reference/GRCh38_reference_genome/GRCh38_full_analysis_set_plus_decoy_hla.fa
cram=https://ftp.sra.ebi.ac.uk/vol1/run/ERR324/ERR3240114/HG00096.final.cram
omni=$kg/release/20130502/supporting/hd_genotype_chip/ALL.chip.omni_broad_sanger_combined.20140818.snps.genotypes.vcf.gz

samtools faidx "$grch38" chr20 | bgzip -c > GRCh38_chr20.fa.gz
samtools faidx GRCh38_chr20.fa.gz
samtools view -C -T GRCh38_chr20.fa.gz -o HG00096.chr20_10-12Mb.cram "$cram" chr20:10000000-12000000
samtools index HG00096.chr20_10-12Mb.cram
bcftools view -s HG00096,HG00097 -r 20:9900000-12100000 -Oz \
    -o omni.HG00096_HG00097.20_9.9-12.1Mb.vcf.gz "$omni"
tabix -p vcf omni.HG00096_HG00097.20_9.9-12.1Mb.vcf.gz
# htslib caches the remote indexes it read; they aren't part of the data.
rm -f HG00096.final.cram.crai ALL.chip.*.tbi
sha256sum GRCh38_chr20.fa.gz* HG00096.chr20_10-12Mb.cram* omni.*
