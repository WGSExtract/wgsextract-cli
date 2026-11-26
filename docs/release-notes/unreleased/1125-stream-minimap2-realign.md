## Changed

- Paired short-read realignment with minimap2 can stream newly extracted reads instead of writing intermediate FASTQs. Existing FASTQs, `--keep-fastq`, and the low-memory file fallback are unchanged ([#1125, streaming disposable intermediates](https://github.com/WGSExtract/wgsextract-cli/issues/1125)).
