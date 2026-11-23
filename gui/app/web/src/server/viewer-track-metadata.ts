import { open, type FileHandle } from "node:fs/promises";
import { gunzipSync } from "node:zlib";
import { ViewerFileError } from "../../../shared/genome-viewer.js";
import { validateTrackRecord } from "../../../shared/viewer-track-records.js";
import { viewerContigAliases } from "./viewer-reference-metadata.js";
import { tabixContigs, type TabixPreset } from "./viewer-tabix-metadata.js";

const limit = 32 * 1024 * 1024;

/** Metadata reads are bounded too: never decompress an entire indexed track. */
class MetadataReader {
    private bytes = 0;
    constructor(private readonly handle: FileHandle, private readonly size: number) {}

    async read(offset: number, length: number): Promise<Buffer> {
        if (!Number.isSafeInteger(offset) || offset < 0 || !Number.isSafeInteger(length) || length < 0 || offset + length > this.size || this.bytes + length > limit) {
            throw new Error("Invalid or oversized metadata.");
        }
        this.bytes += length;
        const buffer = Buffer.alloc(length);
        const { bytesRead } = await this.handle.read(buffer, 0, length, offset);
        if (bytesRead !== length) throw new Error("Truncated metadata.");
        return buffer;
    }
}

export async function validateViewerTrack(file: string, format: "bed" | "bedgraph" | "bigwig", index: string | undefined, reference: Map<string, number>): Promise<void> {
    const handle = await open(file, "r");
    try {
        const size = (await handle.stat()).size;
        const reader = new MetadataReader(handle, size);
        if (format === "bigwig") {
            await validateBigWig(reader, reference, false);
        } else if (index) {
            await validateBgzf(reader);
            const indexed = await tabixContigs(index, size);
            checkContigs(indexed, reference);
        } else {
            if (size > limit) throw new ViewerFileError("trackTooLarge");
            const content = await reader.read(0, size);
            if (content[0] === 31 && content[1] === 139) throw new ViewerFileError("trackCompression");
            validateIntervals(content.toString("utf8"), format, reference);
        }
    } catch (error) {
        if (error instanceof ViewerFileError) throw error;
        throw new ViewerFileError("trackInvalid");
    } finally {
        await handle.close();
    }
}

/** Bounded validation for auto-discovered reference library annotations (allowing chr/no-chr aliases). */
export async function validateReferenceAnnotation(file: string, format: "vcf" | "rsid-tab" | "pharmgkb-tsv" | "bigwig", index: string | undefined, reference: Map<string, number>): Promise<boolean> {
    const handle = await open(file, "r");
    try {
        const size = (await handle.stat()).size;
        const reader = new MetadataReader(handle, size);
        if (format === "bigwig") {
            await validateBigWig(reader, reference, true);
            return true;
        }
        if (!index) return false;
        await validateBgzf(reader);
        const preset: TabixPreset = format === "vcf" ? "vcf" : "rsid-tab";
        const indexed = await tabixContigs(index, size, preset);
        return indexed.some((name) => viewerContigAliases(name).some((alias) => reference.has(alias)));
    } catch {
        return false;
    } finally {
        await handle.close();
    }
}

function checkContigs(names: string[], reference: Map<string, number>) {
    if (!names.length || names.some((name) => !name)) throw new Error("No contigs.");
    if (names.some((name) => !reference.has(name))) throw new ViewerFileError("trackContigs");
}

function validateIntervals(content: string, format: "bed" | "bedgraph", reference: Map<string, number>) {
    let records = 0;
    for (const line of content.split(/\r?\n/)) {
        if (!line.trim() || /^(#|track(?:\s|$)|browser(?:\s|$))/.test(line)) continue;
        validateTrackRecord(line.trim().split(/\s+/), format, reference);
        records++;
    }
    if (!records) throw new Error("Empty track.");
}

async function validateBgzf(reader: MetadataReader) {
    const header = await reader.read(0, 12);
    if (header[0] !== 31 || header[1] !== 139 || header[2] !== 8 || header[3] !== 4) throw new ViewerFileError("trackCompression");
    const extra = await reader.read(12, header.readUInt16LE(10));
    for (let offset = 0; offset + 4 <= extra.length;) {
        const length = extra.readUInt16LE(offset + 2);
        if (offset + 4 + length > extra.length) break;
        if (extra[offset] === 66 && extra[offset + 1] === 67 && length === 2) {
            const block = await reader.read(0, extra.readUInt16LE(offset + 4) + 1);
            if (!gunzipSync(block, { maxOutputLength: 65536 }).length) throw new Error("Empty BGZF block.");
            return;
        }
        offset += 4 + length;
    }
    throw new ViewerFileError("trackCompression");
}

async function validateBigWig(reader: MetadataReader, reference: Map<string, number>, allowAliases: boolean) {
    const header = await reader.read(0, 64);
    const little = header.readUInt32LE(0) === 0x888ffc26;
    const uint32 = (buffer: Buffer, offset: number) => little ? buffer.readUInt32LE(offset) : buffer.readUInt32BE(offset);
    const uint16 = (buffer: Buffer, offset: number) => little ? buffer.readUInt16LE(offset) : buffer.readUInt16BE(offset);
    const uint64 = (buffer: Buffer, offset: number) => Number(little ? buffer.readBigUInt64LE(offset) : buffer.readBigUInt64BE(offset));
    if (uint32(header, 0) !== 0x888ffc26) throw new Error("Not bigWig.");
    const dataOffset = uint64(header, 16);
    const indexOffset = uint64(header, 24);
    if (dataOffset < 64 || indexOffset < dataOffset + 8) throw new Error("Invalid bigWig offsets.");
    const data = await reader.read(dataOffset, 8);
    const index = await reader.read(indexOffset, 48);
    if (!uint64(data, 0) || uint32(index, 0) !== 0x2468ace0 || !uint32(index, 4) || !uint64(index, 8)) {
        throw new Error("Invalid bigWig data/index header.");
    }
    const root = await reader.read(indexOffset + 48, 4);
    const rootCount = uint16(root, 2);
    if (![0, 1].includes(root[0] ?? -1) || !rootCount || rootCount > uint32(index, 4)) throw new Error("Invalid bigWig index root.");
    const rootEntries = await reader.read(indexOffset + 52, rootCount * (root[0] === 1 ? 32 : 24));
    for (let offset = 0; offset < rootEntries.length; offset += root[0] === 1 ? 32 : 24) {
        const child = uint64(rootEntries, offset + 16);
        const length = root[0] === 1 ? uint64(rootEntries, offset + 24) : 4;
        // A bounded read verifies both the offset and the minimum required target header.
        if (!length || (root[0] === 1 && (child < dataOffset + 8 || child + length > indexOffset))) throw new Error("Invalid bigWig index entry.");
        await reader.read(child, Math.min(length, 4));
    }
    const treeOffset = uint64(header, 8);
    const tree = await reader.read(treeOffset, 32);
    if (uint32(tree, 0) !== 0x78ca8c91 || uint32(tree, 12) !== 8) throw new Error("Invalid chromosome tree.");
    const keySize = uint32(tree, 8);
    if (!keySize || keySize > 1024) throw new Error("Invalid chromosome name size.");
    const pending = [treeOffset + 32];
    const visited = new Set<number>();
    let count = 0;
    let matchedCount = 0;
    while (pending.length) {
        const offset = pending.pop();
        if (offset === undefined || visited.has(offset) || visited.size > 100_000) throw new Error("Invalid chromosome tree nodes.");
        visited.add(offset);
        const node = await reader.read(offset, 4);
        if (node[0] !== 0 && node[0] !== 1) throw new Error("Invalid tree node.");
        const entries = await reader.read(offset + 4, uint16(node, 2) * (keySize + 8));
        for (let start = 0; start < entries.length; start += keySize + 8) {
            if (node[0] === 0) {
                pending.push(uint64(entries, start + keySize));
            } else {
                const name = entries.toString("utf8", start, start + keySize).replace(/\0.*$/, "");
                const length = uint32(entries, start + keySize + 4);
                if (allowAliases) {
                    const matches = viewerContigAliases(name).filter((alias) => reference.has(alias));
                    const matched = matches.length === 1 ? matches[0] : undefined;
                    if (matched) {
                        if (reference.get(matched) !== length) throw new ViewerFileError("trackContigs");
                        matchedCount++;
                    }
                } else {
                    checkContigs([name], reference);
                    if (length !== reference.get(name)) throw new ViewerFileError("trackContigs");
                }
                count++;
            }
        }
    }
    if (!count || count !== uint64(tree, 16) || (allowAliases && !matchedCount)) throw new Error("Incomplete chromosome tree.");
}
