## Added

- Gene page now checks required reference files (CRAM reference genome and RefSeq Gene Maps, excluding missing VCF annotations) before running and offers **Cancel**, **Continue**, or **Install and Continue**, with a dedicated sidebar icon ([#1550, GUI UX, user docs, and rendered HTML docs](https://github.com/WGSExtract/wgsextract-cli/pull/1550)).
- Commands with fresh permanent outputs on disk now reuse them directly: result cards open immediately with a **Force Run Again** button, HTML reports open directly, and output-only commands prompt **Cancel** vs. **Force Run Again** (passing `--force` or `--restart` where supported) ([#1550, GUI UX, user docs, and rendered HTML docs](https://github.com/WGSExtract/wgsextract-cli/pull/1550)).
- Separated desktop end-user documentation (`gui/docs/`) from developer documentation (`gui/devdocs/`), added user guides for Basic/Advanced mode, Reference Data, Reference Genomes, Tools, Settings, and Desktop Privacy, and documented the CLI-vs-GUI privacy and network boundary ([#1550, GUI UX, user docs, and rendered HTML docs](https://github.com/WGSExtract/wgsextract-cli/pull/1550)).
- Inline errors, result notices, result summaries, and terminal output now turn `https://wgse.bio/...` URLs and repository doc references into clickable browser links ([#1550, GUI UX, user docs, and rendered HTML docs](https://github.com/WGSExtract/wgsextract-cli/pull/1550)).

## Changed

- Removed the inline **View reads** button below BAM/CRAM selectors and removed the redundant **Reference Genome** dropdown from the Microarray page (automatically detected from the BAM/CRAM) ([#1550, GUI UX, user docs, and rendered HTML docs](https://github.com/WGSExtract/wgsextract-cli/pull/1550)).
- Removed the angled "AI written" corner banner from both documentation sites ([#1550, GUI UX, user docs, and rendered HTML docs](https://github.com/WGSExtract/wgsextract-cli/pull/1550)).
