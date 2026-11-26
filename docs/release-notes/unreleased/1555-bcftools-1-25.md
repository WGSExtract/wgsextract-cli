## Changed

- The Linux x86-64 environments use bcftools 1.25. New calls store average
  mapping quality (`INFO/MQ`) as a floating-point value. Filters that use MQ can
  select different records because they now see the fractional part
  ([#1555, bcftools 1.25 qualification](https://github.com/WGSExtract/wgsextract-cli/issues/1555)).
