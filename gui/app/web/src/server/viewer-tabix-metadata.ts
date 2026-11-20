import { open } from "node:fs/promises";
import { gunzipSync } from "node:zlib";

const limit = 32 * 1024 * 1024;

/** Walk the complete, bounded index, not the potentially genome-sized BGZF data. */
export async function tabixContigs(file: string, dataSize: number): Promise<string[]> {
    const handle = await open(file, "r");
    try {
        const size = (await handle.stat()).size;
        if (size > limit) throw new Error("Oversized tabix metadata.");
        let data = Buffer.alloc(size);
        if ((await handle.read(data, 0, size, 0)).bytesRead !== size) throw new Error("Truncated tabix metadata.");
        if (data[0] === 31 && data[1] === 139) data = gunzipSync(data, { maxOutputLength: limit });
        let offset = 0;
        const take = (length: number) => {
            if (!Number.isSafeInteger(length) || length < 0 || offset + length > data.length) throw new Error("Truncated tabix structure.");
            const result = data.subarray(offset, offset + length);
            offset += length;
            return result;
        };
        const int = () => take(4).readInt32LE(0);
        const count = (minimumBytes: number) => {
            const value = int();
            if (value < 0 || value > Math.floor((data.length - offset) / minimumBytes)) throw new Error("Invalid tabix count.");
            return value;
        };
        const virtual = (value: bigint) => {
            if (value >> 16n >= BigInt(dataSize)) throw new Error("Tabix offset outside data.");
        };
        const magic = take(4).toString("ascii");
        const csi = magic === "CSI\u0001";
        if (!csi && magic !== "TBI\u0001") throw new Error("Not tabix.");
        let references = 0;
        let depth = 5;
        let aux: Buffer;
        if (csi) {
            const minShift = int();
            depth = int();
            if (minShift < 0 || depth < 0 || depth > 10 || minShift + 3 * depth > 53) throw new Error("Invalid CSI bin geometry.");
            aux = take(int());
        } else {
            references = int();
            const header = take(28);
            aux = Buffer.concat([header, take(header.readInt32LE(24))]);
        }
        if (aux.length < 28 || aux.readInt32LE(0) !== 65536 || aux.readInt32LE(4) !== 1 || aux.readInt32LE(8) !== 2 || aux.readInt32LE(12) !== 3) {
            throw new Error("Use tabix BED preset.");
        }
        const namesLength = aux.readInt32LE(24);
        if (namesLength < 1 || 28 + namesLength !== aux.length || aux[aux.length - 1] !== 0) throw new Error("Invalid tabix names.");
        const names = aux.toString("utf8", 28, aux.length - 1).split("\0");
        if (csi) references = int();
        if (references !== names.length || !references || new Set(names).size !== names.length || names.some((name) => !name)
            || references > Math.floor((data.length - offset) / (csi ? 4 : 8))) throw new Error("Invalid tabix references.");
        const maximumBin = (2 ** (3 * (depth + 1)) - 1) / 7;
        for (let ref = 0; ref < references; ref++) {
            const bins = count(csi ? 16 : 8);
            const seen = new Set<number>();
            for (let index = 0; index < bins; index++) {
                const bin = take(4).readUInt32LE(0);
                if (seen.has(bin) || bin > maximumBin + 1 || bin === maximumBin) throw new Error("Invalid tabix bin.");
                seen.add(bin);
                if (csi) virtual(take(8).readBigUInt64LE(0));
                const chunks = count(16);
                if (bin === maximumBin + 1 && chunks !== 2) throw new Error("Invalid tabix statistics.");
                for (let chunk = 0; chunk < chunks; chunk++) {
                    const entry = take(16);
                    if (bin === maximumBin + 1) continue; // Pseudo-bin stores statistics, not data chunks.
                    const start = entry.readBigUInt64LE(0);
                    const end = entry.readBigUInt64LE(8);
                    virtual(start);
                    virtual(end);
                    if (end < start) throw new Error("Reversed tabix chunk.");
                }
            }
            if (!csi) {
                const intervals = count(8);
                for (let index = 0; index < intervals; index++) virtual(take(8).readBigUInt64LE(0));
            }
        }
        if (data.length - offset !== 0 && data.length - offset !== 8) throw new Error("Invalid tabix trailing data.");
        return names;
    } finally {
        await handle.close();
    }
}
