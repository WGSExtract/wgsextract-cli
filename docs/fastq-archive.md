# FASTQ archives

Use `fastq archive` to compress a single FASTQ or a synchronized pair. Use
`fastq restore` before running tools that expect FASTQ, including `align`.
Neither command contacts the network, needs a reference genome, or modifies
its input. They use Zstandard from the Python standard library (3.14+) or
the `backports.zstd` dependency on earlier versions; no external tool is
needed.

```sh
wgsextract fastq archive --r1 reads_R1.fastq.gz --r2 reads_R2.fastq.gz --output reads.wgfq
wgsextract fastq restore --input reads.wgfq --output restored_R1.fastq --output-r2 restored_R2.fastq
```

Omit `--r2` and `--output-r2` for single-end reads. All output paths must be
new and on a filesystem that supports hard links (for example APFS, ext4 or
NTFS). Successful commands also write a result manifest: `fastq_archive`
for the archive and `fastq_r1` / `fastq_r2` for the restored mates.

## What is preserved

Restore writes the same **uncompressed FASTQ bytes** in the same order, per
file. It preserves duplicates, names and comments, all bases and quality
characters, `+` lines, mixed LF/CRLF, and a missing final newline. A gzip/BGZF
input is decompressed first: the original compression wrapper, timestamps,
and block boundaries are not stored. There is no lossy mode.

The input must use four lines per record with a header starting with `@`, a
separator starting with `+`, and equal sequence and quality lengths. Wrapped
records and truncated records are refused. Lines are limited to 8 MiB,
including any terminator, and a single record or pair to 24 MiB in total.
Read 2 must have a matching identifier at every ordinal and the same number
of records as read 1. The identifier is the first whitespace-delimited token
after `@`, ignoring a terminal `/1` or `/2`. Any `/1`, `/2` or CASAVA-style
`1:`/`2:` mate marker must agree with the given mate. Duplicate identifiers
are valid, but identifiers alone cannot distinguish pairs with the same name.

Restore checks the archive before publishing the output files. A checksum
failure, malformed block or wrong number of output paths leaves neither
restored mate published. Keep the source files until you have verified a
round trip and backed up the archive. Checksums detect corruption; they are
not signatures or encryption.

## `.wgfq` version 1 bitstream

All integers are unsigned and little-endian; offsets and sizes are in bytes.
No filenames or filesystem paths occur in the container.

1. The bytes `57 47 53 46 53 01` (`WGSFS`, version 1), then the
   one-byte number of FASTQ files (1 or 2).
2. Zero or more blocks: a `u32` payload size, a `u32` CRC-32 (IEEE, as in
   zlib), and the payload. Blocks carry at most 250,000 records per mate and
   24 MiB of restored FASTQ bytes in total. The encoded payload limit is
   128 MiB. Block and record order is the original order.
3. End marker: `u32` zero and `u32` zero. Next are 32 raw SHA-256 bytes per
   input in mate order, over the entire restored byte stream of that input.
   No unused data may follow.

Each block contains, for each mate in order, four streams: headers,
sequences, `+` lines, and qualities. Every stream is a `u32` transformed
length (at most 24 MiB + 5 bytes), a `u32` compressed length, then that many bytes of a single
independent [Zstandard frame](https://www.rfc-editor.org/rfc/rfc8878) without
a dictionary and with a window at most 64 MiB. Use mode `0` if packing
would exceed the transformed length limit.

Header and `+` streams are the concatenated original lines, with terminators.
Sequences and qualities can be packed: the decompressed stream begins with
one mode byte and a `u32` original stream length, followed by:

- Mode `0`: the original stream, including all terminators.
- Mode `1`: `ceil(original length / 4)` packed bytes, then an array of
  `u32` position deltas and an array of literal bytes. The `i`th two-bit
  value occupies bits `2(i mod 4)` through `2(i mod 4)+1` of its byte.
  The alphabets in code order are `ACGT` for sequences and `F:,#` for
  qualities. A literal replaces the unpacked byte at the position given
  by the cumulative sum of the deltas (the first delta is the zero-based
  position itself). The number of deltas and literals is the size of
  the suffix after the packed bytes divided by five.

Decode each stream, split on LF (keeping any CR), and reassemble one record
at a time by interleaving corresponding lines from each of the four streams.
The first three streams have an LF after every line. The quality stream
may lack its last LF in the final block only; in that case omit the last LF
when reconstructing the block. Nothing depends on matching unique identifiers or normalizing
a base or a quality character.

## Benchmarking

`scripts/fastq_archive_bench.py` compares both prototypes with `bgzip -6`.
It times each encode and decode separately with one codec worker, and checks
each mate with an independent SHA-256 after restoration. Throughput uses
uncompressed FASTQ bytes; bits/base and the BGZF ratio include archive
framing and repairs. The alternative, unaligned CRAM 3.1 plus a sparse
literal repair stream, is benchmark-only and is not a `.wgfq` file.
Benchmarks also require `samtools` 1.24 and `bgzip`. Use `--edge` for the
small byte-exactness fixture and `--records 3000000` for a reproducible
prefix of a local pair. The harness uses an `out/` subdirectory for working
files; the sources are read-only.

On the first 3 million HG002 NovaSeq PCR-free 30× pairs (Apple M1 Pro),
both formats round-tripped exactly. CRAM with repairs produced the smallest
archive (2.413 bits/base), but decoded at 128.2 MB/s in the comparison and
did not reach the 150 MB/s gate in the tuning runs. The shipped format
measured 2.765 bits/base (26.7% smaller than `bgzip -6`), 113.4 MB/s encode
and 246.5 MB/s decode, at 366.7 and 338.1 MiB peak RSS, respectively.
These are slice measurements, not throughput guarantees for other reads,
hosts, or storage.
