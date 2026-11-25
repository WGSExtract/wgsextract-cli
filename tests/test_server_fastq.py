"""Server scheduling for the offline FASTQ archive commands."""

import pytest

from tests.server_data import fake_data as fake_data
from tests.server_data import slow_coverage as slow_coverage
from tests.server_helpers import FAKE_DIR, _serve
from tests.server_helpers import work as work
from tests.smoke_utils import ensure_fake_data as ensure_fake_data
from wgsextract_cli.api.fastq import FastqArchiveParams, archive


@pytest.mark.parametrize("destination", ["archive", "restore-r1", "restore-r2"])
def test_1447_fastq_job_waits_for_each_busy_destination(
    tmp_path, work, fake_data, slow_coverage, destination
):
    bam, ref = fake_data
    other = tmp_path / "other"
    other.mkdir()
    source = other / "reads.fq"
    source.write_bytes(b"@read\nA\n+\n!\n")
    if destination == "archive":
        op = "archive"
        request = {"r1": str(source), "output": str(work / "reads.wgfq")}
    else:
        archived = other / "reads.wgfq"
        mate = other / "mate.fq"
        mate.write_bytes(b"@read\nT\n+\n!\n")
        archive(FastqArchiveParams(r1=source, r2=mate, output=archived))
        op = "restore"
        request = {
            "input": str(archived),
            "output": str((work if destination == "restore-r1" else other) / "r1.fq"),
            "output_r2": str(
                (work if destination == "restore-r2" else other) / "r2.fq"
            ),
        }
    with _serve(tmp_path, [work, other, FAKE_DIR], max_jobs=2) as client:
        busy = {"input": str(bam), "ref": str(ref), "outdir": str(work)}
        first = client.request("POST", "/ops/info/calculate-coverage", busy)[1]
        status, second = client.request("POST", f"/ops/fastq/{op}", request)
        assert status == 202, second
        assert (first["status"], second["status"]) == ("running", "queued")
