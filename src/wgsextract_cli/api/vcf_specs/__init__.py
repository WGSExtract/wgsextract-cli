"""Internal VCF parameter specifications.

Shared field factories and file, calling and annotation declarations back
the public ``api.vcf`` facade. Public entrypoints stay in that facade;
facade aliases retain old pickle lookups while types remain source-discoverable.
"""
