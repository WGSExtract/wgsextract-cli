## Changed

- With GATK 4.6.2.0, selecting whole contigs from an indexed CRAM 3.0 no longer writes a temporary BAM when the CRAI is compatible. Partial intervals and unqualified readers keep the existing staging path ([#1125, streaming disposable intermediates](https://github.com/WGSExtract/wgsextract-cli/issues/1125)).
