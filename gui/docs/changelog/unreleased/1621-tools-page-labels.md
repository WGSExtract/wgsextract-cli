## Changed

- The Tools page labels optional tools that ship with WGS Extract, such as
  fastp, FastQC, samblaster and minimap2, "pre-installed" instead of
  "optional", since they can't be removed on their own ([#1621, VariantQC and Tools labels](https://github.com/WGSExtract/wgsextract-cli/pull/1621)).
- The Tools page no longer shows a certification badge beside each tool's
  Install, Uninstall or check button. The badge was the rating of the
  dependency manager, not the tool, so Java 17 and DISCVRSeq appeared
  Experimental ([#1621, VariantQC and Tools labels](https://github.com/WGSExtract/wgsextract-cli/pull/1621)).
