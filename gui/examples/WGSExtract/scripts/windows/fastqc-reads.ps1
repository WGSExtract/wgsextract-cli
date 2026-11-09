# FastQC on FASTQ R1 and, when given, R2; with --multiqc and both reads, one MultiQC report merging the two.
$ErrorActionPreference = "Stop"

if ($args.Count -lt 1) {
    [Console]::Error.WriteLine("Usage: fastqc-reads.ps1 FASTQ_R1 [--r2 FASTQ_R2] [--outdir DIR] [--multiqc]")
    exit 2
}

$r1 = $args[0]
$r2 = ""
$outDir = ""
$multiqc = $false
$index = 1
while ($index -lt $args.Count) {
    switch ($args[$index]) {
        "--r2" { $r2 = $args[$index + 1]; $index += 2 }
        "--outdir" { $outDir = $args[$index + 1]; $index += 2 }
        "--multiqc" { $multiqc = $true; $index += 1 }
        default { [Console]::Error.WriteLine("Unknown argument: $($args[$index])"); exit 2 }
    }
}
if (-not $outDir) { $outDir = Split-Path -Parent $r1 }
$run = Join-Path (Split-Path -Parent $PSCommandPath) "run-wgsextract.ps1"

& $run qc fastqc --input $r1 --outdir $outDir
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
if ($r2) {
    & $run qc fastqc --input $r2 --outdir $outDir
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    if ($multiqc) {
        & $run qc multiqc --input $outDir --outdir $outDir --force
        exit $LASTEXITCODE
    }
}
exit 0
