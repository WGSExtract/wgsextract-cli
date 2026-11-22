"""``ref acmg-data``: download the sources of ``vcf acmg``'s fastVEP databases
from their publishers and build the databases (issue 629).

Each download is checked against the checksum its publisher gives where there
is one (NCBI and UCSC MD5 files, Google Cloud Storage MD5 hashes, Ensembl's
CHECKSUMS), and every file's SHA-256 goes into the folder's
``acmg_data.json``. The sources are the user's own downloads under their
publishers' terms, which the command shows; WGS Extract never mirrors them.
gnomAD's 877 GB of joint sites are streamed, one chromosome at a time, and
only the INFO keys fastVEP's builder reads are kept. Each database is built in
a staging folder and moved in when complete, and the manifest records it, so
a rerun resumes with what is missing.
"""

from wgsextract_cli.core.acmg_manifest import MANIFEST as MANIFEST
from wgsextract_cli.core.acmg_manifest import SITE_DATA as SITE_DATA
from wgsextract_cli.core.acmg_manifest import load_manifest as load_manifest
from wgsextract_cli.core.acmg_manifest import record_site_data as record_site_data
from wgsextract_cli.core.acmg_manifest import site_data_of as site_data_of

from ._acmg_data_build import (
    Stop as Stop,
)
from ._acmg_data_build import (
    bsd_sum as bsd_sum,
)
from ._acmg_data_build import (
    build as build,
)
from ._acmg_data_build import (
    run_tool as run_tool,
)
from ._acmg_data_build import (
    sa_build as sa_build,
)
from ._acmg_data_build import (
    stream_reduced as stream_reduced,
)
from ._acmg_data_plan import (
    ASSEMBLY as ASSEMBLY,
)
from ._acmg_data_plan import (
    BY_KEY as BY_KEY,
)
from ._acmg_data_plan import (
    CHROMOSOMES as CHROMOSOMES,
)
from ._acmg_data_plan import (
    CLINGEN_URL as CLINGEN_URL,
)
from ._acmg_data_plan import (
    CLINVAR_URL as CLINVAR_URL,
)
from ._acmg_data_plan import (
    CONSTRAINT_URL as CONSTRAINT_URL,
)
from ._acmg_data_plan import (
    ENSEMBL as ENSEMBL,
)
from ._acmg_data_plan import (
    GFF3_NAME as GFF3_NAME,
)
from ._acmg_data_plan import (
    GFF3_TERMS as GFF3_TERMS,
)
from ._acmg_data_plan import (
    GNOMAD_KEEP as GNOMAD_KEEP,
)
from ._acmg_data_plan import (
    GNOMAD_URL as GNOMAD_URL,
)
from ._acmg_data_plan import (
    PER_RUN as PER_RUN,
)
from ._acmg_data_plan import (
    PHYLOP_DIR as PHYLOP_DIR,
)
from ._acmg_data_plan import (
    PHYLOP_FILE as PHYLOP_FILE,
)
from ._acmg_data_plan import (
    REVEL_URL as REVEL_URL,
)
from ._acmg_data_plan import (
    SITES_CACHE as SITES_CACHE,
)
from ._acmg_data_plan import (
    SOURCES as SOURCES,
)
from ._acmg_data_plan import (
    SPLICEAI_URL as SPLICEAI_URL,
)
from ._acmg_data_plan import (
    VARIANT_SUMMARY_URL as VARIANT_SUMMARY_URL,
)
from ._acmg_data_plan import (
    Plan as Plan,
)
from ._acmg_data_plan import (
    Source as Source,
)
from ._acmg_data_plan import (
    Step as Step,
)
from ._acmg_data_plan import (
    acmg_sa_dir as acmg_sa_dir,
)
from ._acmg_data_plan import (
    check_disk as check_disk,
)
from ._acmg_data_plan import (
    default_sa_dir as default_sa_dir,
)
from ._acmg_data_plan import (
    describe as describe,
)
from ._acmg_data_plan import (
    destinations as destinations,
)
from ._acmg_data_plan import (
    fastvep_version as fastvep_version,
)
from ._acmg_data_plan import (
    folder as folder,
)
from ._acmg_data_plan import (
    installed as installed,
)
from ._acmg_data_plan import (
    library_spliceai as library_spliceai,
)
from ._acmg_data_plan import (
    library_spliceai_path as library_spliceai_path,
)
from ._acmg_data_plan import (
    make_plan as make_plan,
)
from ._acmg_data_plan import (
    parse_chromosomes as parse_chromosomes,
)
from ._acmg_data_plan import (
    recorded_gff3 as recorded_gff3,
)
from ._acmg_data_plan import (
    source_url as source_url,
)
from ._acmg_data_sources import (
    clingen_to_genemap as clingen_to_genemap,
)
from ._acmg_data_sources import (
    require_grch38_header as require_grch38_header,
)
from ._acmg_data_sources import (
    require_grch38_vcf as require_grch38_vcf,
)
from ._acmg_data_sources import (
    split_revel as split_revel,
)
from ._acmg_data_sources import (
    tabix_contigs as tabix_contigs,
)
