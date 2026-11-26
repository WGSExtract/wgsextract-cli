## Changed

- New calls from bcftools 1.25 on Linux, macOS, and Windows x64 store average
  mapping quality (`INFO/MQ`) as a Float instead of an Integer. Filters that use
  MQ can select different records because they now see the fractional part
  ([#1555, bcftools 1.25 qualification](https://github.com/WGSExtract/wgsextract-cli/issues/1555)).
- On macOS, Delly now uses a separate environment. Install it with
  `pixi install --locked -e delly` before using `vcf sv` or `vcf cnv` with Delly.
