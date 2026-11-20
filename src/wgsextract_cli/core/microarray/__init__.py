"""Internal microarray calling, export, coordinate and projection helpers.

``coordinates`` projects sites through assembly chains; ``site_map`` decodes
verified bidirectional CHM13 assets; ``site_map_cache`` caches decoded columns.
``chm13*`` modules validate the reference, plan calls, and cache native targets.
``quality`` enforces call evidence; ``quirks`` applies vendor strand policy;
``aadr`` exports pinned allele-aware marker panels.
``templates`` resolves chip templates and writes vendor-format genotypes.
``revisions`` fingerprints consumer-chip exports and ranks catalogue revisions;
``targets`` builds and indexes the union of chip markers on supported assemblies.
The existing ``core.microarray_revisions`` public import interface re-exports
the same objects; new internal consumers import ``microarray.revisions``.
Public operations remain available through ``wgsextract_cli.api.microarray``.
"""
