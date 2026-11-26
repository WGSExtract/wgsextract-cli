"""The config.toml schema: every setting's type, default, allowed values and help.

``Settings`` (and its ``[dev]`` table, ``DevSettings``) is the one place a
setting is declared. Loading, ``wgsextract config``, bug reports and
the published configuration reference all read it. Add a setting as
a field with ``setting(default, help, ...)``; its type annotation decides how
config.toml values are checked.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass, field, fields
from pathlib import Path
from types import MappingProxyType
from typing import Any, Final, Literal, cast, get_type_hints

from wgsextract_cli.core.paths import expand_path
from wgsextract_cli.core.thread_count import parse_thread_count

Kind = Literal["bool", "int", "float", "str", "paths", "table", "threads"]


@dataclass(frozen=True)
class SettingInfo:
    """What config.toml may hold for one setting, beyond its Python type."""

    help: str
    choices: tuple[str, ...] = ()
    # Compare choices case-insensitively, as the setting's readers do.
    fold_case: bool = False
    minimum: float | None = None
    maximum: float | None = None
    # Expand a leading ~ when the value is read.
    path: bool = False


def setting(
    default: Any,
    help: str,
    *,
    choices: tuple[str, ...] = (),
    fold_case: bool = False,
    minimum: float | None = None,
    maximum: float | None = None,
    path: bool = False,
) -> Any:
    """A ``Settings`` field and its config.toml rules."""
    info = SettingInfo(help, choices, fold_case, minimum, maximum, path)
    return field(default=default, metadata={"setting": info})


AUTO_OFF = ("auto", "off")
# --reference-evidence: none, or a gVCF beside the variant VCF (#863).
REFERENCE_EVIDENCE_MODES = ("none", "gvcf")


@dataclass(frozen=True)
class DevSettings:
    """The ``[dev]`` table: developer and test settings, not for end users."""

    pet_r1_fastq: str | None = setting(None, "Path to PET R1 reads", path=True)
    pet_r2_fastq: str | None = setting(None, "Path to PET R2 reads", path=True)
    pet_reference_fasta: str | None = setting(
        None, "Path to PET reference genome", path=True
    )
    real_genome_test_path: str | None = setting(
        None, "Path to a local real genome dataset", path=True
    )
    real_genome_tests_enabled: bool = setting(
        False, "Enable real smoke tests from real_genome_test_path"
    )
    download_cache: bool = setting(
        False, "Cache downloads under $XDG_CACHE_HOME/wgsextract for reuse"
    )
    download_cache_ttl_seconds: int = setting(
        7 * 24 * 60 * 60,
        "Seconds before an unused download cache item is pruned",
        minimum=0,
    )


@dataclass(frozen=True)
class ServerSettings:
    """Operator opt-ins for the HTTP/JSON listener."""

    listen: str = setting(
        "127.0.0.1",
        "HTTP listen address[:port]; explicit --port overrides the embedded port; loopback by default",
    )
    allowed_hosts: str | tuple[str, ...] | None = setting(
        None, "Exact external Host headers, including ports; never wildcards"
    )
    allowed_origins: str | tuple[str, ...] | None = setting(
        None, "Exact app origins allowed to read viewer files; never wildcards"
    )
    viewer_files: bool = setting(
        False, "Enable authenticated, guarded viewer byte ranges"
    )
    public_url: str | None = setting(
        None,
        "Advertised HTTP/HTTPS client origin; required for non-loopback listening, with its authority in allowed_hosts",
    )
    max_connections: int = setting(
        32,
        "Maximum accepted HTTP connections, including unauthenticated peers",
        minimum=1,
        maximum=256,
    )
    header_timeout_seconds: float = setting(
        10.0,
        "Absolute request-header and keep-alive idle deadline; does not limit authenticated responses",
        minimum=0.1,
        maximum=60,
    )


@dataclass(frozen=True)
class Settings:
    """Every config.toml setting, checked and with its default filled in."""

    certification_notices: bool = setting(
        True, "Log a notice when running commands at the Tested or Experimental level"
    )
    allow_reference_downloads: bool = setting(
        False,
        "Allow first-use public reference/model downloads for commands with "
        "--download (hosts and request data: docs/privacy.md); no sample uploads",
    )
    cohort_min_samples: int = setting(
        21,
        "Cohort disclosure minimum for cohort size and callable participants",
        minimum=1,
    )
    cohort_min_allele_count: int = setting(
        10, "Cohort disclosure minimum for called ALT copies", minimum=1
    )
    cohort_min_carriers: int = setting(
        10,
        "Cohort disclosure minimum for both carriers and callable non-carriers",
        minimum=1,
    )
    cohort_min_relatedness: float = setting(
        0.09,
        "Cohort warning cutoff on the qc relate scale (twice kinship)",
        minimum=0.0,
        maximum=1.0,
    )
    input_path: str | None = setting(
        None, "Default input BAM/CRAM or FASTQ file", path=True
    )
    output_directory: str | None = setting(
        None,
        "Default output directory (default: WGSEv6/ beside the primary input BAM, "
        "CRAM, FASTQ, VCF or microarray file, or that folder if already WGSEv6/)",
        path=True,
    )
    temp_directory: str | None = setting(
        None,
        "Temporary Workload directory: every command's temporary work (sort spills, staging, scratch files)",
        path=True,
    )
    atomic_outputs: bool = setting(
        False,
        "Publish completed main outputs by rename (changes tool provenance paths)",
    )
    reference_fasta: str | None = setting(
        None, "Path to the reference genome FASTA", path=True
    )
    reference_library: str | None = setting(
        None,
        "Reference data directory (annotation data, gene maps, VEP caches)",
        path=True,
    )
    reference_genome_library: str | None = setting(
        None,
        "Folder holding only the reference genomes (FASTAs) and the files made "
        "from them, for reference_library (default: genomes/ in it)",
        path=True,
    )
    mygenomes_library: str | None = setting(
        None,
        "My Genomes library: one folder per person or sample (default: "
        "mygenomes/ in the users workspace; formerly dataset_library)",
        path=True,
    )
    cpu_threads: int | str = setting(
        "auto",
        "CPU threads: a positive integer for every stage, or auto "
        "(per-stage core-layout plan on Apple Silicon; all cores elsewhere)",
    )
    memory_limit: str = setting(
        "auto",
        "Memory limit per thread, e.g. 2G (auto: 25% of RAM split across threads)",
    )
    debug_mode: bool = setting(False, "Enable verbose debug logging")
    quiet_mode: bool = setting(False, "Enable quiet mode (errors only)")
    log_dir: str | None = setting(
        None,
        "Opt-in directory for unique persistent DEBUG run logs and provenance; "
        "it keeps the newest 100 runs",
        path=True,
    )
    state_directory: str | None = setting(
        None,
        "Folder for the last-run record and saved bug reports (report-issue); "
        "default: state/ in the data folder",
        path=True,
    )
    server_port: int = setting(
        0,
        "Default HTTP/JSON listen port (0: any free port); an embedded server.listen port overrides this default, and explicit --port wins",
        minimum=0,
        maximum=65535,
    )
    server_allowed_paths: str | tuple[str, ...] | None = setting(
        None,
        "Folders (a string or an array) `wgsextract serve` requests may read "
        "and write, besides mygenomes_library, reference_library, "
        "reference_genome_library and output_directory",
        path=True,
    )
    server_mcp_transport: str = setting(
        "stdio",
        "How `wgsextract serve --mcp` speaks MCP: stdio, or http (streamable "
        "HTTP on 127.0.0.1 with a per-launch token; needs the optional 'mcp' "
        "package)",
        choices=("stdio", "http"),
    )
    server_mcp_port: int = setting(
        0,
        "Port `wgsextract serve --mcp-transport http` listens on at 127.0.0.1 "
        "(0: any free port)",
        minimum=0,
        maximum=65535,
    )
    server_max_jobs: int = setting(
        1,
        "Long `wgsextract serve` jobs that run at once; more wait in a queue",
        minimum=1,
    )
    keep_awake: bool = setting(
        False, "Best-effort sleep inhibition while commands run (opt-in)"
    )
    result_card: bool = setting(
        False,
        "Also write `<name>.card.html`, a shareable page of each run's result, "
        "beside its manifest",
    )
    haplogrep_executable: str | None = setting(
        None, "Path to the Haplogrep 3 executable or JAR", path=True
    )
    dorado_executable: str | None = setting(
        None, "Path to ONT's dorado executable for `basecall`", path=True
    )
    discvrseq_jar: str | None = setting(
        None, "Path to DISCVRseq.jar for `vcf variantqc`", path=True
    )
    java17_executable: str | None = setting(
        None,
        "Path to a Java 17 binary for tools requiring Java 17 (such as `vcf variantqc`)",
        path=True,
    )
    granite_executable: str | None = setting(
        None, "Path to the granite executable for `vcf granite-qc`", path=True
    )
    native_aligners: str | None = setting(
        None,
        "Apple Silicon: `align`/`realign` use wgse-bwa, wgse-bwa-mem2, "
        "wgse-bwa-mem3 and wgse-minimap2 when on PATH (auto, the default) or "
        "never (off); Linux: Bioconda's bwa-mem3 in place of bwa/bwa-mem2; "
        "Windows x86-64: use wgse-bwa and wgse-minimap2 when installed",
        choices=AUTO_OFF,
        fold_case=True,
    )
    wgse_bwa_path: str | None = setting(
        None, "Path to an optional native Windows x86-64 wgse-bwa executable", path=True
    )
    output_profile: str = setting(
        "default",
        "`align`/`realign`: default keeps --format; archive selects lossless "
        "CRAM 3.1 (GATK 4.7+); calling selects compatible CRAM 3.0",
        choices=("default", "archive", "calling"),
    )
    prefer_native_gpu: bool | None = setting(
        None,
        "`align`/`realign`: on Windows, native CUDA is automatic when it fits; "
        "set false to force CPU. Elsewhere, set true to prefer "
        "wgse-bwa's native GPU even above the RAM "
        "budget after normal sort step-down; retain explicit memory, otherwise "
        "cap default/config sort at 256M (may swap heavily)",
    )
    dense_suffix_array: bool = setting(
        False,
        "`align`/`realign`: wgse-bwa reads the reference's dense suffix-array "
        "file from `ref dense-sa` (opt-in; used only when wgse-bwa aligns; "
        "holds it in RAM, 0.8 GB for hs38)",
    )
    dorado_models_directory: str | None = setting(
        None,
        "Folder `basecall` downloads Dorado models into (default: the "
        "wgsextract cache's dorado-models)",
        path=True,
    )
    mimalloc: str | None = setting(
        None,
        "Apple Silicon: `vcf snp`/`vcf indel` run bcftools with WGS Extract's "
        "wgse-mimalloc allocator. Native Windows: `bam to-cram --cram-version 3.1` "
        "with a local reference uses the matching samtools-mimalloc encoder. "
        "Only validated installed variants run (auto, default), or never (off)",
        choices=AUTO_OFF,
        fold_case=True,
    )
    baq: bool = setting(
        False,
        "`vcf snp`/`vcf indel`: enable heuristic base alignment quality "
        "(BAQ) in bcftools mpileup (slower; may reduce low-depth recall); "
        "off by default; see docs/variant-confidence.md",
    )
    reference_evidence: str = setting(
        "none",
        "`vcf snp`/`vcf indel`/`vcf deepvariant`/`vcf gatk`/`vcf clair3`: also "
        "write a gVCF of reference evidence (bcftools.g.vcf.gz, "
        "deepvariant.g.vcf.gz, gatk.g.vcf.gz, clair3.g.vcf.gz) beside "
        "the variant VCF (gvcf), or not (none, the default); see "
        "docs/reference-evidence.md",
        choices=REFERENCE_EVIDENCE_MODES,
    )
    haplocheck_executable: str | None = setting(
        None, "Path to the haplocheck executable or JAR", path=True
    )
    report_gene_list: str | None = setting(
        None,
        "`report`: genes of the ClinVar section, a built-in list "
        "(acmg-sf-3.3, the default, or acmg-sf-3.2) or a TSV or BED file",
    )
    haplogrep_tree: str | None = setting(
        None,
        "Haplogrep 3 tree for mtDNA haplogroups: phylotree-fu-rcrs "
        "(default), phylotree-rcrs or mitotree-rcrs",
    )
    y_tree: str | None = setting(
        None,
        "Y haplogroup tree for `lineage y-haplogroup`: yfull (default), "
        "yfull_v10, ftdna, isogg or a [y_trees] id",
    )
    # Each tree's own fields are checked when the tree is used.
    y_trees: Mapping[str, Mapping[str, Any]] | None = setting(
        None,
        "Your own Y haplogroup trees, one [y_trees.<id>] table each "
        "(see the Yleaf README)",
    )
    jar_directory: str | None = setting(None, "Directory for JAR tools", path=True)
    vep_cache_directory: str | None = setting(None, "Path to VEP cache", path=True)
    vep_engine: str | None = setting(
        None,
        "Engine `vep run` annotates with: vep-rs (default) or ensembl",
        choices=("vep-rs", "ensembl"),
    )
    pharmcat_directory: str | None = setting(
        None, "Extracted PharmCAT pipeline release", path=True
    )
    parabricks_image: str | None = setting(
        None,
        "NVIDIA Parabricks Docker image for GPU alignment and calling "
        "(default nvcr.io/nvidia/clara/clara-parabricks:4.8.0-1)",
    )
    deepvariant_image: str | None = setting(
        None,
        "DeepVariant Docker image for CPU calling (default "
        "google/deepvariant:1.10.0 on x64; local "
        "localhost/wgsextract-deepvariant:1.10.0-arm64 on Linux ARM64)",
    )
    deepvariant_native: str | None = setting(
        None,
        "`vcf deepvariant` runs wgse-deepvariant (Apple Silicon, Linux x86-64) "
        "when it is on PATH (auto, the default) or never (off)",
        choices=AUTO_OFF,
        fold_case=True,
    )
    deepvariant_weights: str | None = setting(
        None,
        "Folder of the weights wgse-deepvariant calls with (from its "
        "macos-deepvariant or linux-deepvariant release)",
        path=True,
    )
    deepvariant_ultima_image: str | None = setting(
        None,
        "DeepVariant Docker image with the ULTIMA model, for Ultima reads "
        "(default google/deepvariant:gh1100-ultima)",
    )
    deepvariant_ultima_gpu_image: str | None = setting(
        None,
        "DeepVariant GPU Docker image with the ULTIMA model, for `vcf "
        "deepvariant --gpu` on Ultima reads (default "
        "google/deepvariant:gh1100-ultima-gpu, pinned by digest)",
    )
    clair3_model: str | None = setting(
        None,
        "Clair3 ONT model for `vcf clair3`: a model directory or the name of one "
        "Clair3 bundles (default: chosen from the reads' basecaller metadata)",
    )
    clair3_gpu_image: str | None = setting(
        None,
        "Clair3 GPU Docker image for `vcf clair3 --gpu` (default "
        "hkubal/clair3:v2.0.5_gpu, pinned by digest)",
    )
    methylation_context: str = setting(
        "cpg",
        "`methylation pileup` context: cpg (CpG sites) or all (every cytosine)",
        choices=("cpg", "all"),
    )
    methylation_5hmc: bool = setting(
        False, "`methylation pileup`: also report 5hmC (Nanopore 5mCG_5hmCG models)"
    )
    methylation_combine_strands: bool = setting(
        True, "`methylation pileup`: sum both strands of each CpG into one row"
    )
    default_input_vcf: str | None = setting(None, "Default input VCF file", path=True)
    microarray_min_gq: int = setting(
        20, "Microarray minimum sample GQ (0 disables filtering)", minimum=0
    )
    microarray_min_dp: int = setting(
        10, "Microarray minimum sample DP (0 disables filtering)", minimum=0
    )
    microarray_assume_hom_ref: bool = setting(
        False,
        "Treat absent VCF markers on represented contigs as homozygous reference",
    )
    microarray_exclude_filtered: bool = setting(
        True,
        "Write microarray no-calls for VCF FILTER values other than PASS or .",
    )
    microarray_call_reasons: bool = setting(
        False,
        "Write a companion CombinedKit_reasons.tsv with explicit call/no-call reasons",
    )
    microarray_strand_ambiguous: str = setting(
        "keep",
        "Keep or no-call known biallelic A/T and C/G input SNP sites",
        choices=("keep", "no-call"),
    )
    microarray_resolve_rsids: bool = setting(
        False,
        "Resolve packaged dbSNP rsID aliases and no-call retired/ambiguous IDs",
    )
    microarray_quirks: str = setting(
        "corrected",
        "Write microarray calls on the forward strand (corrected) or as the "
        "vendor reported known reverse-strand markers (vendor)",
        choices=("corrected", "vendor"),
    )
    qc_min_diploid_depth: float = setting(
        15.0,
        "Warn before microarray/VCF genotypes below this mean autosomal depth "
        "(0 disables)",
        minimum=0,
    )
    qc_min_haploid_depth: float = setting(
        3.0,
        "Warn before chrY/mtDNA haplogroups below this mean depth (0 disables)",
        minimum=0,
    )
    qc_min_autosomal_homozygosity: float = setting(
        0.6,
        "Warn when fewer microarray autosomal calls are homozygous (contamination)",
        minimum=0,
        maximum=1,
    )
    qc_max_autosomal_homozygosity: float = setting(
        0.88,
        "Warn when more microarray autosomal calls are homozygous (low-pass "
        "or heavily imputed input)",
        minimum=0,
        maximum=1,
    )
    qc_max_no_call_rate: float = setting(
        0.1,
        "Warn when a microarray output has more autosomal no-calls than this",
        minimum=0,
        maximum=1,
    )
    qc_max_contamination: float = setting(
        0.03,
        "Warn, and cap confidence labels at moderate, when `qc contamination` "
        "estimates more of the reads (FREEMIX) come from another person",
        minimum=0,
        maximum=1,
    )
    confidence_min_depth: float = setting(
        10.0,
        "Label rare/clinical variant calls below this read depth low confidence "
        "(halved for haploid calls)",
        minimum=0,
    )
    confidence_high_depth: float = setting(
        20.0,
        "Label rare/clinical variant calls below this read depth (and at least "
        "confidence_min_depth) moderate confidence",
        minimum=0,
    )
    confidence_min_gq: float = setting(
        20.0,
        "Label rare/clinical variant calls below this genotype quality low confidence",
        minimum=0,
    )
    confidence_high_gq: float = setting(
        30.0,
        "Label rare/clinical variant calls below this genotype quality (and at "
        "least confidence_min_gq) moderate confidence",
        minimum=0,
    )
    mother_vcf_path: str | None = setting(
        None, "Default Mother VCF for trios", path=True
    )
    father_vcf_path: str | None = setting(
        None, "Default Father VCF for trios", path=True
    )
    batch_file_path: str | None = setting(
        None, "Path to batch processing CSV/TSV", path=True
    )
    vcf_input_paths: str | tuple[str, ...] | None = setting(
        None, "Multiple VCF inputs", path=True
    )
    clinvar_vcf_path: str | None = setting(None, "Path to ClinVar VCF data", path=True)
    revel_tsv_path: str | None = setting(None, "Path to REVEL TSV data", path=True)
    phylop_tsv_path: str | None = setting(
        None, "Path to PhyloP bigWig (or TSV/VCF) data", path=True
    )
    cadd_tsv_path: str | None = setting(
        None, "Path to CADD whole-genome SNV TSV data", path=True
    )
    cadd_indel_tsv_path: str | None = setting(
        None, "Path to CADD indel TSV data", path=True
    )
    gerp_bigwig_path: str | None = setting(
        None, "Path to GERP++ RS bigWig data", path=True
    )
    gnomad_vcf_path: str | None = setting(None, "Path to gnomAD VCF data", path=True)
    acmg_sa_dir: str | None = setting(
        None, "Folder of fastVEP annotation databases for vcf acmg", path=True
    )
    acmg_gff3: str | None = setting(
        None, "Ensembl GFF3 of transcript models for vcf acmg", path=True
    )
    gnomad_sv_path: str | None = setting(
        None, "Path to a gnomAD-SV sites table (ref gnomad-sv)", path=True
    )
    gnomad_lr_sv_path: str | None = setting(
        None,
        "Path to a gnomAD-LR SV sites table (ref gnomad-sv --source lr)",
        path=True,
    )
    spliceai_vcf_path: str | None = setting(
        None, "Path to SpliceAI VCF data", path=True
    )
    alphamissense_vcf_path: str | None = setting(
        None, "Path to AlphaMissense VCF data", path=True
    )
    pharmgkb_vcf_path: str | None = setting(None, "Path to PharmGKB data", path=True)
    signing_key: str | None = setting(
        None, "OpenSSH private key used by `sign create`", path=True
    )
    pgs_scoring_file: str | None = setting(
        None, "Custom scoring TSV[.gz], overriding Catalog weights", path=True
    )
    pgs_min_match: float = setting(
        0.75,
        "Minimum PGS match fraction for percentile eligibility",
        minimum=0,
        maximum=1,
    )
    pgs_keep_ambiguous: bool = setting(
        False,
        "Keep palindromic SNPs only with independently verified forward-strand alleles",
    )
    dev: DevSettings = field(default_factory=DevSettings)
    server: ServerSettings = field(default_factory=ServerSettings)


@dataclass(frozen=True)
class SettingField:
    """One setting of the schema, as loading and the docs see it."""

    name: str
    kind: Kind
    default: object
    info: SettingInfo

    @property
    def expected(self) -> str:
        """What a valid value is, for an error message."""
        if self.info.choices:
            return "one of " + ", ".join(self.info.choices)
        low, high = self.info.minimum, self.info.maximum
        if self.kind in ("int", "float"):
            noun = "TOML integer" if self.kind == "int" else "TOML number"
            if low is not None and high is not None:
                return f"a {noun} from {low:g} to {high:g}"
            if low == 0:
                return f"a non-negative {noun}"
            if low is not None:
                return f"a {noun} of at least {low:g}"
            return f"a {noun}"
        return {
            "bool": "a TOML boolean (true or false)",
            "str": "a TOML string",
            "paths": "a TOML string or an array of TOML strings",
            "table": "a TOML table of [y_trees.<id>] tables",
            "threads": "a positive TOML integer, positive integer string, or 'auto'",
        }[self.kind]

    def check(self, value: object) -> object:
        """``value`` as the setting holds it (paths expanded, arrays as tuples).
        Raises ValueError, whose text is what was found, if it isn't valid."""
        kind = self.kind
        if kind == "bool":
            ok = type(value) is bool
        elif kind == "int":
            ok = type(value) is int and self._in_range(value)
        elif kind == "float":
            ok = type(value) in (int, float) and self._in_range(value)
            if ok:
                value = float(cast(float, value))
        elif kind == "threads":
            ok = type(value) is int or isinstance(value, str)
            if ok:
                try:
                    parse_thread_count(value)
                except ValueError:
                    ok = False
        elif kind == "paths":
            if isinstance(value, list | tuple):
                ok = all(isinstance(item, str | os.PathLike) for item in value)
                value = tuple(_path(item) for item in value) if ok else value
            else:
                ok = isinstance(value, str | os.PathLike)
                value = _path(value) if ok else value
        elif kind == "table":
            ok = isinstance(value, Mapping) and all(
                isinstance(entry, Mapping) for entry in value.values()
            )
        else:
            ok = (
                isinstance(value, str | os.PathLike)
                if self.info.path
                else (isinstance(value, str))
            )
            if ok and self.info.path:
                value = _path(value)
            if ok and self.info.choices:
                assert isinstance(value, str)
                text = value.lower() if self.info.fold_case else value
                ok = text in self.info.choices
        if not ok:
            raise ValueError(_found(value))
        return value

    def _in_range(self, value: object) -> bool:
        assert isinstance(value, int | float)
        low, high = self.info.minimum, self.info.maximum
        return (low is None or value >= low) and (high is None or value <= high)


def _path(value: object) -> str:
    if isinstance(value, Path):
        return str(value.expanduser())
    assert isinstance(value, str | os.PathLike)
    return expand_path(value)


def _found(value: object) -> str:
    if isinstance(value, bool | int | float | str):
        return f"{type(value).__name__} {value!r}"
    return type(value).__name__


_KINDS: tuple[tuple[object, Kind], ...] = (
    (bool, "bool"),
    (bool | None, "bool"),
    (int, "int"),
    (float, "float"),
    (str, "str"),
    (str | None, "str"),
    (int | str, "threads"),
    (str | tuple[str, ...] | None, "paths"),
    (Mapping[str, Mapping[str, Any]] | None, "table"),
)


def _kind(name: str, annotation: object) -> Kind:
    for known, kind in _KINDS:
        if annotation == known:
            return kind
    raise TypeError(f"Setting {name} has an unsupported type: {annotation}")


def _schema(cls: type) -> Mapping[str, SettingField]:
    hints = get_type_hints(cls)
    schema = {}
    for item in fields(cls):
        info = item.metadata.get("setting")
        if isinstance(info, SettingInfo):
            schema[item.name] = SettingField(
                item.name, _kind(item.name, hints[item.name]), item.default, info
            )
    return MappingProxyType(schema)


SETTINGS: Final = _schema(Settings)
DEV_SETTINGS: Final = _schema(DevSettings)
SERVER_SETTINGS: Final = _schema(ServerSettings)
PATH_SETTINGS = frozenset(name for name, item in SETTINGS.items() if item.info.path)
DEV_PATH_SETTINGS = frozenset(
    name for name, item in DEV_SETTINGS.items() if item.info.path
)
DEV_TABLE: Final = "dev"
# Dev settings were top-level keys before the [dev] table; name the new place.
MOVED_TO_DEV: Final = MappingProxyType(
    {
        **{name: name for name in DEV_SETTINGS if not name.startswith("download_")},
        "dev_download_cache": "download_cache",
        "dev_download_cache_ttl_seconds": "download_cache_ttl_seconds",
    }
)


class InvalidSetting(ValueError):
    """A config.toml value of the wrong type or out of range."""

    def __init__(self, key: str, setting: SettingField, found: str) -> None:
        super().__init__(f"expected {setting.expected}; got {found}.")
        self.key = key
        self.setting = setting
        self.found = found


def check_values(
    values: Mapping[str, object],
    schema: Mapping[str, SettingField],
    canonical: Callable[[str], str] = str,
    *,
    strict_threads: bool = True,
) -> Iterator[tuple[str, str, object]]:
    """``(key, setting name, checked value)`` for each known, non-None entry
    of ``values``. Raises InvalidSetting for the first one that isn't valid."""
    for key, value in values.items():
        name = canonical(key)
        setting_field = schema.get(name)
        if setting_field is None or value is None:
            continue
        try:
            checked = setting_field.check(value)
        except ValueError as error:
            if setting_field.kind == "threads" and not strict_threads:
                continue
            raise InvalidSetting(key, setting_field, str(error)) from None
        yield key, name, checked
