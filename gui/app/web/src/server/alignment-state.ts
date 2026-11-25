import { open, stat, type FileHandle } from "node:fs/promises";
import path from "node:path";
import { gunzipSync } from "node:zlib";
import { errnoCode } from "./errors.js";

const maxReadBytes = 4 * 1024 * 1024;
const maxUncompressedBytes = 4 * 1024 * 1024;
const maxHeaderBytes = 256 * 1024;
const bgzfMaxBlockBytes = 65_536;
const cramMagic = "CRAM";
const bamMagic = "BAM\x01";
const indexedAlignmentExtensions = new Set([".bam", ".cram"]);
const sortedAlignmentExtensions = new Set([".bam", ".cram", ".sam"]);

/** Checks whether a matching BAM/CRAM index file exists alongside the alignment file. */
export async function isIndexedAlignment(resolvedPath: string): Promise<boolean> {
    const ext = path.extname(resolvedPath).toLowerCase();
    if (!indexedAlignmentExtensions.has(ext)) {
        return false;
    }
    const parsed = path.parse(resolvedPath);
    const withoutExtension = path.join(parsed.dir, parsed.name);
    const suffixes = ext === ".bam" ? [".bai", ".csi"] : [".crai", ".csi"];
    const candidates = suffixes.flatMap((suffix) => [`${resolvedPath}${suffix}`, `${withoutExtension}${suffix}`]);
    return (await Promise.all(candidates.map((candidate) => isRegularFile(candidate)))).some(Boolean);
}

/** Reads the BAM, CRAM, or SAM header to check whether `@HD` declares `SO:coordinate`. */
export async function isSortedAlignment(resolvedPath: string): Promise<boolean> {
    const ext = path.extname(resolvedPath).toLowerCase();
    if (!sortedAlignmentExtensions.has(ext)) {
        return false;
    }
    try {
        const header = await readAlignmentHeader(resolvedPath);
        return isCoordinateSortedHeader(header);
    } catch {
        return false;
    }
}

/** True when a SAM header contains an `@HD` line with the `SO:coordinate` tag. */
export function isCoordinateSortedHeader(headerText: string): boolean {
    if (!headerText) {
        return false;
    }
    for (const line of headerText.split(/\r?\n/)) {
        if (!line.startsWith("@HD")) {
            continue;
        }
        const fields = line.split("\t");
        if (fields[0] === "@HD" && fields.includes("SO:coordinate")) {
            return true;
        }
    }
    return false;
}

async function readAlignmentHeader(filePath: string): Promise<string> {
    const handle = await open(filePath, "r");
    try {
        const info = await handle.stat();
        if (!info.isFile() || info.size < 4) {
            return "";
        }
        const prefix = Buffer.alloc(Math.min(4, info.size));
        const { bytesRead } = await handle.read(prefix, 0, prefix.length, 0);
        if (bytesRead < 4) {
            return "";
        }
        if (prefix[0] === 0x1f && prefix[1] === 0x8b) {
            return await readBamHeader(handle, info.size);
        }
        if (prefix.toString("ascii", 0, 4) === cramMagic) {
            return await readCramHeader(handle, info.size);
        }
        if (prefix[0] === 0x40) {
            const length = Math.min(info.size, maxHeaderBytes);
            const buffer = Buffer.alloc(length);
            const read = await handle.read(buffer, 0, length, 0);
            return buffer.toString("utf8", 0, read.bytesRead);
        }
        return "";
    } finally {
        await handle.close();
    }
}

async function readBamHeader(handle: FileHandle, fileSize: number): Promise<string> {
    const chunks: Buffer[] = [];
    let uncompressedBytes = 0;
    let offset = 0;
    let targetBytes = 8 + maxHeaderBytes;
    let parsedHeaderLength = false;

    while (offset < fileSize && uncompressedBytes < targetBytes && offset < maxReadBytes) {
        if (offset + 12 > fileSize) {
            break;
        }
        const fixed = await readExact(handle, offset, 12);
        if (!fixed || fixed[0] !== 0x1f || fixed[1] !== 0x8b || fixed[2] !== 8 || (fixed[3]! & 4) === 0) {
            break;
        }
        const xlen = fixed.readUInt16LE(10);
        if (offset + 12 + xlen > fileSize) {
            break;
        }
        const extra = await readExact(handle, offset + 12, xlen);
        if (!extra) {
            break;
        }
        const blockSize = bgzfBlockSize(extra);
        if (blockSize == null || blockSize < 18 || offset + blockSize > fileSize) {
            break;
        }
        const block = await readExact(handle, offset, blockSize);
        if (!block) {
            break;
        }
        const data = gunzipSync(block, { maxOutputLength: bgzfMaxBlockBytes });
        chunks.push(data);
        uncompressedBytes += data.length;
        offset += blockSize;

        if (!parsedHeaderLength && uncompressedBytes >= 8) {
            const head = Buffer.concat(chunks);
            if (head.toString("binary", 0, 4) !== bamMagic) {
                return "";
            }
            const headerLength = head.readUInt32LE(4);
            targetBytes = 8 + Math.min(headerLength, maxHeaderBytes);
            parsedHeaderLength = true;
        }
    }

    if (!parsedHeaderLength) {
        return "";
    }
    const combined = Buffer.concat(chunks);
    const end = Math.min(targetBytes, combined.length);
    return combined.toString("utf8", 8, end);
}

function bgzfBlockSize(extra: Buffer): number | null {
    for (let offset = 0; offset + 4 <= extra.length;) {
        const subLen = extra.readUInt16LE(offset + 2);
        if (offset + 4 + subLen > extra.length) {
            return null;
        }
        if (extra[offset] === 66 && extra[offset + 1] === 67 && subLen === 2) {
            return extra.readUInt16LE(offset + 4) + 1;
        }
        offset += 4 + subLen;
    }
    return null;
}

async function readCramHeader(handle: FileHandle, fileSize: number): Promise<string> {
    if (fileSize < 26) {
        return "";
    }
    const cursor = new BoundedFileCursor(handle, fileSize, maxReadBytes);
    const definition = await cursor.readBytes(26);
    if (definition.toString("ascii", 0, 4) !== cramMagic) {
        return "";
    }
    const major = definition[4]!;
    if (major < 2 || major > 3) {
        return "";
    }

    // Container 0 holds the SAM file header.
    await cursor.readBytes(4); // container byte length
    await cursor.readItf8(); // reference sequence id
    await cursor.readItf8(); // starting position
    await cursor.readItf8(); // alignment span
    await cursor.readItf8(); // number of records
    if (major >= 3) {
        await cursor.skipLtf8(); // record counter
        await cursor.skipLtf8(); // bases
    } else {
        await cursor.readItf8(); // record counter
        await cursor.readItf8(); // bases
    }
    await cursor.readItf8(); // number of blocks
    const landmarkCount = await cursor.readItf8();
    if (landmarkCount < 0 || landmarkCount > 100_000) {
        return "";
    }
    for (let index = 0; index < landmarkCount; index++) {
        await cursor.readItf8();
    }
    if (major >= 3) {
        await cursor.readBytes(4); // container header CRC32
    }

    // Block 0 inside Container 0 is the FILE_HEADER block.
    const method = await cursor.readByte();
    const contentType = await cursor.readByte();
    if (contentType !== 0) {
        return "";
    }
    await cursor.readItf8(); // content id
    const compressedSize = await cursor.readItf8();
    const rawSize = await cursor.readItf8();
    if (compressedSize < 0 || compressedSize > maxReadBytes || rawSize < 0 || rawSize > maxUncompressedBytes) {
        return "";
    }
    const payload = await cursor.readBytes(compressedSize);
    const uncompressed = method === 0
        ? payload
        : method === 1
            ? gunzipSync(payload, { maxOutputLength: maxUncompressedBytes })
            : null;
    if (!uncompressed || uncompressed.length < 4) {
        return "";
    }
    const headerLength = uncompressed.readInt32LE(0);
    if (headerLength <= 0) {
        return "";
    }
    const end = Math.min(4 + headerLength, uncompressed.length, 4 + maxHeaderBytes);
    return uncompressed.toString("utf8", 4, end);
}

class BoundedFileCursor {
    private buffer: Buffer = Buffer.alloc(0);
    private cursor = 0;
    private fileOffset = 0;
    private totalRead = 0;

    constructor(
        private readonly handle: FileHandle,
        private readonly fileSize: number,
        private readonly limit: number,
    ) {}

    private async ensure(needed: number): Promise<void> {
        const remaining = this.buffer.length - this.cursor;
        if (remaining >= needed) {
            return;
        }
        const toRead = Math.min(
            Math.max(needed - remaining, 16_384),
            this.fileSize - this.fileOffset,
            this.limit - this.totalRead,
        );
        if (remaining + toRead < needed) {
            throw new Error("Unexpected end of file or read limit exceeded.");
        }
        const chunk = Buffer.alloc(toRead);
        const { bytesRead } = await this.handle.read(chunk, 0, toRead, this.fileOffset);
        if (bytesRead === 0 || remaining + bytesRead < needed) {
            throw new Error("Truncated alignment header.");
        }
        this.fileOffset += bytesRead;
        this.totalRead += bytesRead;
        const tail = this.cursor > 0 ? this.buffer.subarray(this.cursor) : this.buffer;
        this.buffer = tail.length ? Buffer.concat([tail, chunk.subarray(0, bytesRead)]) : chunk.subarray(0, bytesRead);
        this.cursor = 0;
    }

    async readByte(): Promise<number> {
        await this.ensure(1);
        return this.buffer[this.cursor++]!;
    }

    async readBytes(length: number): Promise<Buffer> {
        if (length === 0) {
            return Buffer.alloc(0);
        }
        await this.ensure(length);
        const slice = Buffer.from(this.buffer.subarray(this.cursor, this.cursor + length));
        this.cursor += length;
        return slice;
    }

    async readItf8(): Promise<number> {
        const b0 = await this.readByte();
        if ((b0 & 0x80) === 0) {
            return b0;
        }
        if ((b0 & 0xc0) === 0x80) {
            const b1 = await this.readByte();
            return ((b0 & 0x3f) << 8) | b1;
        }
        if ((b0 & 0xe0) === 0xc0) {
            const rest = await this.readBytes(2);
            return ((b0 & 0x1f) << 16) | (rest[0]! << 8) | rest[1]!;
        }
        if ((b0 & 0xf0) === 0xe0) {
            const rest = await this.readBytes(3);
            return ((b0 & 0x0f) << 24) | (rest[0]! << 16) | (rest[1]! << 8) | rest[2]!;
        }
        const rest = await this.readBytes(4);
        return ((b0 & 0x0f) << 28) | (rest[0]! << 20) | (rest[1]! << 12) | (rest[2]! << 4) | (rest[3]! & 0x0f);
    }

    async skipLtf8(): Promise<void> {
        const b0 = await this.readByte();
        let extra = 0;
        for (let bit = 0x80; bit > 0 && (b0 & bit) !== 0; bit >>= 1) {
            extra++;
        }
        if (extra > 0) {
            await this.readBytes(extra);
        }
    }
}

async function readExact(handle: FileHandle, offset: number, length: number): Promise<Buffer | null> {
    const buffer = Buffer.alloc(length);
    const { bytesRead } = await handle.read(buffer, 0, length, offset);
    return bytesRead === length ? buffer : null;
}

async function isRegularFile(filePath: string): Promise<boolean> {
    try {
        const info = await stat(filePath);
        return info.isFile();
    } catch (error) {
        if (errnoCode(error) === "ENOENT" || errnoCode(error) === "ENOTDIR") {
            return false;
        }
        throw error;
    }
}
