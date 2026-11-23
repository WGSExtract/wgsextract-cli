SEQUENCERS = {
    "Illumina NS 6000 (Dante)": r"^(A00910|A00925|A00966|A01245):\d+:[a-zA-Z0-9]{9}:\d:\d+:\d+:\d+$",
    "Illumina NS 6000 (FTDNA)": r"^(A00186):\d+:[a-zA-Z0-9]{9}:\d:\d+:\d+:\d+$",
    "Illumina NS 6000 (GeneDX)": r"^(A00571):\d+:[a-zA-Z0-9]{9}:\d:\d+:\d+:\d+$",
    "Illumina NS 6000 (Illum)": r"^(A00582):\d+:[a-zA-Z0-9]{9}:\d:\d+:\d+:\d+$",
    "Illumina Novaseq 6000": r"^(M_)?(A\d{5}):\d+:[a-zA-Z0-9]{9}:\d:\d+:\d+:\d+$",
    "Illumina Novaseq X": r"^(LH\d{5}):\d+:[a-zA-Z0-9]{9}:\d:\d+:\d+:\d+$",
    "Illumina NextSeq 500/550": r"^(N[BS]\d{6}):\d+:[a-zA-Z0-9]{9}:\d:\d+:\d+:\d+$",
    "Illumina NextSeq 2000": r"^(V\d{5}):\d+:[a-zA-Z0-9]{9}:\d:\d+:\d+:\d+$",
    "Solexa GAI (HiSeq 1000)": r"^(HWI-C\d{3,5}):\d+:[a-zA-Z0-9]{9}:\d:\d+:\d+:\d+$",
    "Illumina HiSeq 1000": r"^(C\d{3,5}):\d+:[a-zA-Z0-9]{9}:\d:\d+:\d+:\d+$",
    "Solexa GAII (HiSeq 2000)": r"^(HWI-ST\d{3,5}):\d+:[a-zA-Z0-9]{9}:\d:\d+:\d+:\d+$",
    "Solexa GAII (HiSeq 2500)": r"^(HWI-D\d{3,5}):\d+:[a-zA-Z0-9]{9}:\d:\d+:\d+:\d+$",
    "Illumina HiSeq 2500": r"^(D\d{3,5}):\d+:[a-zA-Z0-9]{9}:\d:\d+:\d+:\d+$",
    "Solexa GAIIx (HiSeq 3000)": r"^(HWI-J\d{3,5}):\d+:[a-zA-Z0-9]{9}:\d:\d+:\d+:\d+$",
    "Illumina HiSeq 3000": r"^(J\d{3,5}):\d+:[a-zA-Z0-9]{9}:\d:\d+:\d+:\d+$",
    "Illumina HiSeq 4000": r"^(K\d{3,5}):\d+:[a-zA-Z0-9]{9}:\d:\d+:\d+:\d+$",
    "Solexa GAxx? (FTDNA)": r"^(SN\d{6,8}):\d+:[a-zA-Z0-9]{9}:\d:\d+:\d+:\d+",
    "Illumina HiSeq X": r"^(ST[-_])?(E\d{3,5}):\d+:[a-zA-Z0-9]{9}:\d:\d+:\d+:\d+$",
    "Illumina MiniSeq": r"^(MN\d{3,5}):\d+:[a-zA-Z0-9]{9}:\d:\d+:\d+:\d+$",
    "Solexa GA (MiSeq)": r"^(HWI-M\d{3,5}):\d+:[a-zA-Z0-9]{9}:\d:\d+:\d+:\d+$",
    "Illumina MiSeq": r"^(M\d{3,5}):\d+:[a-zA-Z0-9]{9}:\d:\d+:\d+:\d+$",
    # Older Bases2Fastq runs can retain comma-separated flow-cell barcode fields.
    "Element AVITI": (
        r"^(?:AV[0-9]{6}|[A-Za-z0-9_-]+_AVITI):[A-Za-z0-9_-]+:"
        r"(?:[A-Za-z0-9]+(?:,[A-Za-z0-9]+(?:-[A-Za-z0-9]+)*)*|FC-[A-Za-z0-9_-]+)"
        r":[12]:[0-9]+:[0-9]{4}:[0-9]{4}"
        r"(?::[ACGTN]+(?:\+[ACGTN]+)?)?(?:/[12])?$"
    ),
    "MGI DNB T7 (ProPhase)": r"^(PRO\d{7})(_[A-Za-z0-9_]{25,29}_)(E100\d{6})(L\d)(C\d{3})(R\d{3})(\d{6,8})([/1]?|[/2]?)$",
    "MGI DNB T7 (ProPhase-bug)": r"^(<NEBULA_ID>)(_[A-Za-z0-9_]{25,29}_)(E100\d{6})(L\d)(C\d{3})(R\d{3})(\d{6,8})([/1]?|[/2]?)$",
    "MGI DNB T7 (ProPhase 2)": r"^(N[A-Z0-9]{9}_[0-9_]{17}_ProPhase_BP_)(E\d{9})(L\d)(C\d{3})(R\d{3})(\d{6,8})([/1]?|[/2]?)$",
    "MGI DNB T7 (Dante, 6xxx)": r"^(E200006\d{3})(L\d)(C\d{3})(R\d{3})(\d{6,8})([/1]?|[/2]?)$",
    "MGI DNB T7 v1": r"^(E1\d{8})(L\d)(C\d{3})(R\d{3})(\d{6,8})([/1]?|[/2]?)$",
    "MGI DNB T7 v2x": r"^(E2\d{8})(L\d)(C\d{3})(R\d{3})(\d{6,8})([/1]?|[/2]?)$",
    "MGI DNB T10 (ProPhase)": r"^(PRO\d{7})(_[A-Za-z0-9_]{25,29}_)(FP200\d{6})(L\d)(C\d{3})(R\d{3})(\d{6,8})([/1]?|[/2]?)$",
    "MGI DNB T10 (ProPhase-bug)": r"^(<NEBULA_ID>)(_[A-Za-z0-9_]{25,29}_)(FP200\d{6})(L\d)(C\d{3})(R\d{3})(\d{6,8})([/1]?|[/2]?)$",
    "MGI DNB T10 (ProPhase 2)": r"^(N[A-Z0-9]{9}_[0-9_]{17}_ProPhase_BP_)(FP2\d{8})(L\d)(C\d{3})(R\d{3})(\d{6,8})([/1]?|[/2]?)$",
    "MGI DNB T10 v1": r"^(FP20\d{7})(L\d)(C\d{3})(R\d{3})(\d{6,8})([/1]?|[/2]?)$",
    "MGI DNB T10 v2.7": r"^(FP27\d{7})(L\d)(C\d{3})(R\d{3})(\d{6,8})([/1]?|[/2]?)$",
    "MGI DNB G400": r"^(V3[05]0\d{6})(L\d)(C\d{3})(R\d{3})(\d{6,8})([/1]?|[/2]?)$",
    "MGI DNB G400 (ML)": r"^(ML\d{9})(L\d)(C\d{3})(R\d{3})(\d{6,8})([/1]?|[/2]?)$",
    "MGI DNB G400 FAST": r"^([CV]L?100\d{6})(L\d)(C\d{3})(R\d{3})_?(\d{6,8})([/1]?|[/2]?)$",
    "MGI DNB G400 (YSEQ)": r"^(YSEQ1):\d+:[A-Z0-9]{10,12}:\d:\d{5,7}:\d{3}:\d{3}$",
    "PacBio HiFi Revio": r"^(m\d{5}e?)_\d{6}_\d{6}(_s\d+)?/\d+/ccs(?:$|/)",
    # Ultima's own CRAMs carry the barcode (038266_2-Z0138-3185054028);
    # MyHeritage's leave it out (608478_2-3317701967).
    "Ultima Genomics UG 100": r"^(\d{6,}(?:_\d+)?)-(?:([A-Z]\d{4,})-)?(\d{10})$",
    "Oxford Nanopore": r"^([0-9a-f]{8})-([0-9a-f]{4})-([0-9a-f]{4})-([0-9a-f]{4})-([0-9a-f]{12})$",
    "Unknown": r"^[\S]+$",
}

REFERENCE_MODELS = {
    "ee4efe40ebd6f9468dab89963dcc5b65": ["hg19", "Yoruba", "hg19.fa.gz"],
    "eed540239f34de0b80734add1779cb5b": ["hg19p13", "Yoruba", "hg19.p13.plusMT.fa.gz"],
    "8ed6919c076595f09e1028325dc86fbe": [
        "hg19p13a",
        "rCRS",
        "hg19.p13.plusMT.full_analysis_set.fa.gz",
    ],
    "5a923ccfc7ee30aebe864817653d41d8": [
        "hg19p13n",
        "rCRS",
        "hg19.p13.plusMT.no_alt_analysis_set.fa.gz",
    ],
    "9b3bf038da5fef087723525d5174f0bd": ["hg37", "rCRS", "hg19_yseq.fa.gz"],
    "50093bf43ed53848c9c7696752e65c2f": ["hg37w", "rCRS", "hg19_WGSE.fa.gz"],
    "bd894134bddba260df88a90123a2ee9c": ["hg38", "rCRS", "hg38.fa.gz"],
    "f5abbfc73ccdb2e8356003d230bce0ce": ["hg38p12", "rCRS", "hg38p12.fa.gz"],
    "88b5f4bbaaec4a097b3aa160bf776093": [
        "hs37-",
        "rCRS",
        "human_g1k_v37.fasta.gz",
    ],
    "92c90b84dac3d9d8402bf2f2dbe95961": ["hs37d5", "rCRS", "hs37d5.fa.gz"],
    "f8812b68f2aa4aa592e742e081f82807": [
        "GRCh37",
        "rCRS",
        "Homo_sapiens.GRCh37.dna.toplevel.fa.gz",
    ],
    "abf297943fca66859d781349e8e28687": [
        "GRCh38",
        "rCRS",
        "Homo_sapiens.GRCh38.99.dna.toplevel.fa.gz",
    ],
    "5c38f3f6774ece1a75c4d5d55010ced7": [
        "GRCh37p13",
        "rCRS",
        "GRCh37.p13.genome.fa.gz",
    ],
    "a08daf6f9f22170759705fd99e471b62": [
        "hs38DH",
        "rCRS",
        "hs38DH.fa.gz",
    ],
    "42c06a6446741565da8cae7bbeb67184": [
        "GRCh38p13",
        "rCRS",
        "GRCh38.p13.genome.fa.gz",
    ],
    "236b4fa639deaaa51691eb16ea92a0f9": [
        "hs37a",
        "rCRS",
        "GCA_000001405.14_GRCh37.p13_full_analysis_set.fna.gz",
    ],
    "0b94854439441bc45d1b061eedb2ac0e": [
        "hs37n",
        "rCRS",
        "GCA_000001405.14_GRCh37.p13_no_alt_analysis_set.fna.gz",
    ],
    "53f744f1771eabfded06778564b931bc": [
        "hs38a",
        "rCRS",
        "hs38a.fna.gz",
    ],
    "c9ef5f042a951d511567dd4ea9bd126e": [
        "hs38",
        "rCRS",
        "hs38.fa.gz",
    ],
    "ee77d771adff64d7ee6357316ee24b6b": [
        "hs38d1",
        "rCRS",
        "hs38d1.fna.gz",
    ],
    "567b0b7efc021c69f64fe63d85dbc797": [
        "Dog_UU_GSD",
        "MT",
        "GCF_011100685.1_UU_Cfam_GSD_1.0_genomic.fna.gz",
    ],
    "492b4726909f048464fdd00232f63f47": [
        "Cat_Fca126",
        "MT",
        "GCF_018350175.1_F.catus_Fca126_mat1.0_genomic.fna.gz",
    ],
    "ed9f80c8e04c461cba35dd1b85a2e2e8": ["T2Tv20", "rCRS", "chm13v2.0.fa.gz"],
    # Reference catalog genomes (seed_genomes.csv); compute a new one's with
    # scripts/reference_signatures.py.
    "2777429fbb9f8cbcde449ac60b1b8613": ["hs38d1a", "rCRS", "hs38d1a.fna.gz"],
    "14b392c68a6a34d0083b360d734eb5f7": ["hs37", "rCRS", "hs37.fa.gz"],
    "89cbafdbfcd4369f5aaa3bb34911125d": ["hs38d1s", "rCRS", "hs38d1s.fa.gz"],
    "fae44dd244ce77ff30499b64b8e008a8": ["hs38d1t", "rCRS", "hs38d1t.fa.gz"],
    "7bdc7b6c9928b422cacc4244f7d448cb": ["hs38d1c", "rCRS", "hs38d1c.fa.gz"],
    "d52b52352eb5bb59ac527a918131bf2d": ["THGySeqp", "rCRS", "hg38_CP086569.fasta.gz"],
    "38482ccb4720d61b3caceaf0cb2c0df0": [
        "GRCh37-",
        "rCRS",
        "GRCh37.primary_assembly.genome.fa.gz",
    ],
    "3c38d6e133ff02bf1e3d982f03d19054": [
        "GRCh38-",
        "rCRS",
        "GRCh38.primary_assembly.genome.fa.gz",
    ],
    "3b16431997472dd3de17f58cc8d73fc4": ["T2Tv20a", "rCRS", "GCA_009914755.4.fna.gz"],
    "0dfc6af84589531ba6c7c7a2bfda581f": ["HPPv11", "rCRS", "CHM13v11Y.fa.gz"],
    "18f6472fda529a5edf8398711464ab9f": ["T2Tv11", "rCRS", "chm13.draft_v1.1.fasta.gz"],
    "89fe3fb609c16630417a0bc64035058f": ["T2Tv10", "rCRS", "chm13.draft_v1.0.fasta.gz"],
    "5c36e82a6c3dc291a08722144aaa6b6a": ["T2Tv09", "rCRS", "chm13.draft_v0.9.fasta.gz"],
    # T2T HG002 and T2T PR1 (HG01243) models.
    "8219a5fb530e4ec7963d240fc97876a5": ["THGv27", "rCRS", "hg002xy_v2.7.fasta.gz"],
    "502f4ec6812385d2f9e6d7e19a29730d": ["THGv20", "rCRS", "hg002xy_v2.fasta.gz"],
    "19f89605b2479e947a7cd571fdf70750": ["THG1243v3", "", "hg01243_v3.fna.gz"],
    # Ensembl's current GRCh38 toplevel (as of 2026-10), detection only: its
    # contigs change between releases, so the catalog records no signature.
    "1999a42a0b6740994754efb5aa556f8e": [
        "GRCh38",
        "rCRS",
        "Homo_sapiens.GRCh38.dna.toplevel.fa.gz",
    ],
}

# Reference model classification tags (MinorClass + Build + SN naming, e.g. 1K38h,
# hg19h, EBI37g; and T2T CHM13 / T2T PR1 / T2T HG002 for post-scheme T2T models).
MODEL_TAGS_BY_CODE: dict[str, str] = {
    "hg19": "hg19h",
    "hg19p13": "hg19ht",
    "hg19p13a": "1K37ht",
    "hg19p13n": "1K37ht",
    "hg37": "hg37h",
    "hg37w": "EBI37h",
    "GRCh37-": "EBI37h",
    "GRCh37": "EBI37g",
    "GRCh37p13": "hg37h",
    "hs37-": "1K37g",
    "hs37": "1K37g",
    "hs37d5": "1K37g",
    "hs37a": "1K37h",
    "hs37n": "1K37h",
    "hg38": "hg38h",
    "hg38p12": "hg38h",
    "GRCh38-": "hg38h",
    "GRCh38p13": "hg38h",
    "GRCh38": "EBI38g",
    "hs38": "1K38h",
    "hs38a": "1K38h",
    "hs38d1": "1K38h",
    "hs38d1a": "1K38h",
    "hs38DH": "1K38h",
    "hs38d1c": "1K38ph",
    "hs38d1v": "1K38ph",
    "hs38d1s": "1K38pg",
    "hs38d1t": "1K38pg",
    "T2Tv20": "T2T CHM13",
    "T2Tv20a": "T2T CHM13",
    "T2Tv11": "T2T CHM13",
    "T2Tv10": "T2T CHM13",
    "T2Tv09": "T2T CHM13",
    "HPPv11": "T2T CHM13",
    "HPPv1": "T2T CHM13",
    "THG1243v3": "T2T PR1",
    "THGv27": "T2T HG002",
    "THGv20": "T2T HG002",
    "THGySeqp": "T2T HG002",
    "Dog_UU_GSD": "Dog",
    "UU_Cfam_GSD_1.0": "Dog",
    "Cat_Fca126": "Cat",
    "Fca126_mat1.0": "Cat",
}

# Likely reference by @SQ count: [legacy flag, FASTA, mito name, human build or
# "" for other species]. Only counts that identify one build belong here: 25 (chr1-22,
# X, Y, M) describes hg19, hg38 and T2T dictionaries alike.
REFGEN_BY_SNCOUNT = {
    2197: [
        True,
        "GCF_011100685.1_UU_Cfam_GSD_1.0_genomic.fna.gz",
        "MT",
        "",
    ],  # Dog (UU_Cfam_GSD_1.0, incl. unplaced)
    71: [
        True,
        "GCF_018350175.1_F.catus_Fca126_mat1.0_genomic.fna.gz",
        "MT",
        "",
    ],  # Cat (Fca126)
    84: [True, "human_g1k_v37.fasta.gz", "MT", "hg19"],
    85: [
        False,
        "GCA_000001405.14_GRCh37.p13_no_alt_analysis_set.fna.gz",
        "chrM",
        "hg19",
    ],
    86: [True, "hs37d5.fa.gz", "MT", "hg19"],
    93: [False, "hg19.fa.gz", "chrM", "hg19"],
    195: [True, "GCA_000001405.15_GRCh38_no_alt_analysis_set.fna.gz", "chrM", "hg38"],
    297: [False, "Homo_sapiens.GRCh37.75.dna.toplevel.fa.gz", "MT", "hg19"],
    298: [
        False,
        "GCA_000001405.14_GRCh37.p13_full_analysis_set.fna.gz",
        "chrM",
        "hg19",
    ],
    389: [False, "Homo_sapiens.GRCh38.89.dna.toplevel.fa.gz", "MT", "hg38"],
    455: [False, "hg38.fa.gz", "chrM", "hg38"],
    456: [False, "GCA_000001405.15_GRCh38_full_analysis_set.fna.gz", "chrM", "hg38"],
    524: [False, "Homo_sapiens.GRCh38.86.dna.toplevel.fa.gz", "MT", "hg38"],
    555: [False, "Homo_sapiens.GRCh38.91i.dna.toplevel.fa.gz", "MT", "hg38"],
    593: [False, "Homo_sapiens.GRCh38.97i.dna.toplevel.fa.gz", "MT", "hg38"],
    595: [True, "hg38p12.fa.gz", "chrM", "hg38"],
    639: [False, "Homo_sapiens.GRCh38.current_fasta.dna.toplevel.fa.gz", "MT", "hg38"],
    2580: [
        False,
        "GCA_000001405.15_GRCh38_no_alt_plus_hs38d1_analysis_set.fna.gz",
        "chrM",
        "hg38",
    ],
    2581: [False, "hs38d1s.fa.gz", "MT", "hg38"],
    2582: [False, "hs38d1t.fa.gz", "MT", "hg38"],
    2779: [False, "hs38d1c.fa.gz", "chrM", "hg38"],
    2841: [
        False,
        "GCA_000001405.15_GRCh38_full_plus_hs38d1_analysis_set.fna.gz",
        "chrM",
        "hg38",
    ],
    3366: [False, "GRCh38_full_analysis_set_plus_decoy_hla.fa.gz", "chrM", "hg38"],
}

REF_GENOME_FILENAMES = {
    "hg19": "hg19.fa.gz",
    "hg37": "hg19_yseq.fa.gz",
    "hg37w": "hg19_WGSE.fa.gz",
    "hs37-": "human_g1k_v37.fasta.gz",
    "hs37": "hs37.fa.gz",
    "hs37d5": "hs37d5.fa.gz",
    "GRCh37-": "GRCh37.primary_assembly.genome.fa.gz",
    "GRCh37": "Homo_sapiens.GRCh37.dna.toplevel.fa.gz",
    "hg38": "hg38.fa.gz",
    "hs38": "hs38.fa.gz",
    "hs38a": "hs38a.fna.gz",
    "hs38d1": "hs38d1.fna.gz",
    "hs38d1s": "hs38d1s.fa.gz",
    "hs38d1t": "hs38d1t.fa.gz",
    "hs38d1c": "hs38d1c.fa.gz",
    "hs38d1v": "GRCh38_Verily_v1.genome.fa.gz",
    "hs38d1a": "hs38d1a.fna.gz",
    "hs38DH": "hs38DH.fa.gz",
    "GRCh38": "Homo_sapiens.GRCh38.dna.toplevel.fa.gz",
    "GRCh38-": "GRCh38.primary_assembly.genome.fa.gz",
    "THGv27": "hg002xy_v2.7.fasta.gz",
    "THGv20": "hg002xy_v2.fasta.gz",
    "HPPv11": "CHM13v11Y.fa.gz",
    "HPPv1": "CHM13v1Y.fa.gz",
    "THG1243v3": "hg01243_v3.fna.gz",
    "THGySeqp": "hg38_CP086569.fasta.gz",
    "T2Tv20": "chm13v2.0.fa.gz",
    "T2Tv20a": "GCA_009914755.4.fna.gz",
    "T2Tv11": "chm13.draft_v1.1.fasta.gz",
    "T2Tv10": "chm13.draft_v1.0.fasta.gz",
    "T2Tv09": "chm13.draft_v0.9.fasta.gz",
    "Dog_UU_GSD": "GCF_011100685.1_UU_Cfam_GSD_1.0_genomic.fna.gz",
    "Cat_Fca126": "GCF_018350175.1_F.catus_Fca126_mat1.0_genomic.fna.gz",
}

# Reference library bootstrap, built by scripts/build_reference_bootstrap.py.
# Since v0.2.0 it carries no YBrowse data (#509); `ref ybrowse --install` fetches it.
BOOTSTRAP_VERSION = "0.2.0"
BOOTSTRAP_URL = f"https://github.com/WGSExtract/wgsextract-cli/releases/download/reference-bootstrap-v{BOOTSTRAP_VERSION}/wgsextract-reference-bootstrap-{BOOTSTRAP_VERSION}.tar.gz"
BOOTSTRAP_SHA256 = "03d7f58ba813c12e1efed048466f62959dc51f996d95aace76105c49fefca8e9"
BOOTSTRAP_FILENAME = "wgsextract-reference-bootstrap.tar.gz"

# Delly mappability map files `ref status` reports (Delly 2 no longer needs them).
MAPPABILITY_MAP_FILES = (
    "hg19.map.gz",
    "hg19.map.gz.fai",
    "hg19.map.gz.gzi",
    "hg38.map.gz",
    "hg38.map.gz.fai",
    "hg38.map.gz.gzi",
)
