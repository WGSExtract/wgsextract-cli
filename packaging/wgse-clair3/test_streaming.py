"""Exercise the installed caller with synthetic reads/weights, faking only MPS."""

import argparse
import gzip
import json
import os
import random
import shutil
import subprocess
import sys
from pathlib import Path


def prepare(work: Path, launcher: Path) -> None:
    import torch

    root = launcher.parent.parent / "share" / "wgse-clair3"
    sys.path.insert(0, str(root))
    from clair3.model import Clair3_F, Clair3_P
    from clair3.task.gt21 import GT21_LABELS_MAP
    from shared.param_f import ont_input_shape as alignment_shape
    from shared.param_p import ont_input_shape as pileup_shape

    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True, exist_ok=True)
    model = work / "model"
    model.mkdir(exist_ok=True)
    torch.manual_seed(1106)
    rng = random.Random(1106)
    sequence = "".join(rng.choices("ACGT", k=3000))
    alternate = "A" if sequence[999] != "A" else "C"
    for filename, network in (
        ("pileup.pt", Clair3_P(input_channels=pileup_shape[-1])),
        (
            "full_alignment.pt",
            Clair3_F(add_indel_length=True, input_channels=alignment_shape[-1]),
        ),
    ):
        with torch.no_grad():
            for parameter in network.parameters():
                parameter.zero_()
            network.Y_gt21_logits.bias[GT21_LABELS_MAP[alternate * 2]] = 10
            network.Y_genotype_logits.bias[1] = 10
            if network.add_indel_length:
                network.Y_indel_length_logits_1.bias[16] = 10
                network.Y_indel_length_logits_2.bias[16] = 10
        torch.save(network.state_dict(), model / filename)
    reference = work / "hg38.fa"
    reference.write_text(
        "".join(f">{chrom}\n{sequence}\n" for chrom in ("chr20", "chrX", "chrY"))
    )
    subprocess.run(["samtools", "faidx", str(reference)], check=True)
    sample = sequence[:999] + alternate + sequence[1000:]
    sam = work / "reads.sam"
    rows = [
        "@HD\tVN:1.6\tSO:coordinate",
        "@SQ\tSN:chr20\tLN:3000",
        "@SQ\tSN:chrX\tLN:3000",
        "@SQ\tSN:chrY\tLN:3000",
        "@RG\tID:r\tSM:S\tPL:ONT",
    ]
    for chrom in ("chr20", "chrX", "chrY"):
        for i in range(40):
            start = 900 + i
            rows.append(
                f"{chrom}_{i}\t0\t{chrom}\t{start}\t60\t200M\t*\t0\t0\t"
                f"{sample[start - 1 : start + 199]}\t{'I' * 200}\tRG:Z:r"
            )
    sam.write_text("\n".join(rows) + "\n")
    bam = work / "reads.bam"
    subprocess.run(["samtools", "sort", "-o", str(bam), str(sam)], check=True)
    subprocess.run(["samtools", "index", str(bam)], check=True)
    boundary = work / "gpu-boundary"
    boundary.mkdir(exist_ok=True)
    hostile = work / "foreign-python"
    hostile.mkdir()
    foreign_python = hostile / "python3"
    foreign_python.write_text("#!/bin/sh\nexit 97\n")
    foreign_python.chmod(0o755)
    # Redirect only device transfers/checkpoint placement, retaining real feature
    # generation, model loading, inference, calling, workers and gVCF generation.
    (boundary / "sitecustomize.py").write_text(
        'import importlib.util\nif importlib.util.find_spec("torch"):\n    import gpu_boundary\n'
    )
    (boundary / "gpu_boundary.py").write_text(
        """
import json, sys
from pathlib import Path
import torch
torch.backends.mps.is_available = lambda: True
original_module_to = torch.nn.Module.to
original_tensor_to = torch.Tensor.to
original_load = torch.load
def module_to(self, *args, **kwargs):
    if args and str(args[0]) == "mps":
        with Path(__file__).with_name("devices.jsonl").open("a") as out:
            out.write(json.dumps({"argv": sys.argv, "device": str(args[0])}) + "\\n")
        def predicted(module, inputs, output):
            with Path(__file__).with_name("predicted.jsonl").open("a") as out:
                out.write(json.dumps({"argv": sys.argv, "batch": len(inputs[0])}) + "\\n")
        self.register_forward_hook(predicted)
        args = (torch.device("cpu"), *args[1:])
    return original_module_to(self, *args, **kwargs)
def tensor_to(self, *args, **kwargs):
    if args and str(args[0]) == "mps":
        args = (torch.device("cpu"), *args[1:])
    return original_tensor_to(self, *args, **kwargs)
def load(*args, **kwargs):
    if str(kwargs.get("map_location")) == "mps":
        kwargs["map_location"] = torch.device("cpu")
    return original_load(*args, **kwargs)
torch.nn.Module.to = module_to
torch.Tensor.to = tensor_to
torch.load = load
"""
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--prepare-only", type=Path)
    args = parser.parse_args()
    launcher = Path(shutil.which("wgse-clair3") or "")
    work = (args.prepare_only or Path("out/streaming-regression")).resolve()
    prepare(work, launcher)
    if args.prepare_only:
        return
    model, reference, bam = work / "model", work / "hg38.fa", work / "reads.bam"
    boundary, hostile = work / "gpu-boundary", work / "foreign-python"
    for gvcf in (False, True):
        output = work / ("gvcf" if gvcf else "normal")
        log = boundary / "devices.jsonl"
        log.unlink(missing_ok=True)
        predictions = boundary / "predicted.jsonl"
        predictions.unlink(missing_ok=True)
        command = [
            str(launcher),
            f"--bam_fn={bam}",
            f"--ref_fn={reference}",
            f"--model_path={model}",
            f"--output={output}",
            "--threads=1",
            "--platform=ont",
            "--device=mps",
            "--use_gpu=False",
            "--no_phasing_for_fa",
            "--haploid_precise",
            "--ctg_name=chrX",
        ]
        if gvcf:
            command += ["--gvcf", "--disable_c_impl"]
        subprocess.run(
            command,
            check=True,
            env={
                **os.environ,
                "PYTHONPATH": str(boundary),
                "PATH": f"{hostile}{os.pathsep}{os.environ['PATH']}",
            },
            stdin=subprocess.DEVNULL,
        )
        observed = [json.loads(line) for line in log.read_text().splitlines()]
        expected = "CallVariants" if gvcf else "CallVariantsFromCffi"
        assert any(Path(entry["argv"][0]).stem == expected for entry in observed), (
            observed
        )
        assert all(entry["device"] == "mps" for entry in observed)
        assert any(
            json.loads(line)["batch"] > 0
            for line in predictions.read_text().splitlines()
        )
        vcf = output / "merge_output.vcf.gz"
        assert vcf.is_file(), list(output.iterdir())
        with gzip.open(vcf, "rt") as source:
            records = [line.split("\t") for line in source if not line.startswith("#")]
        assert len(records) == 1, records
        assert records[0][0:2] == ["chrX", "1000"]
        assert records[0][9].split(":")[0] == "1"
        if gvcf:
            with gzip.open(output / "merge_output.gvcf.gz", "rt") as source:
                blocks = [
                    line.split("\t") for line in source if not line.startswith("#")
                ]
            assert any(row[4] == "<NON_REF>" for row in blocks)
    print("Packaged normal and gVCF streaming reached MPS model transfers.")


if __name__ == "__main__":
    main()
