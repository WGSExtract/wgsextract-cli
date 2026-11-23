import { ViewerFileError, type LocalTrack } from "./genome-viewer.js";

export function validateTrackRecord(columns: string[], format: "bed" | "bedgraph", reference: Map<string, number>): void {
    const [contig = "", first = "", last = "", value = ""] = columns;
    const length = reference.get(contig);
    if (length === undefined) throw new ViewerFileError("trackContigs");
    const start = Number(first);
    const end = Number(last);
    if (!/^\d+$/.test(first) || !/^\d+$/.test(last) || !Number.isSafeInteger(start) || !Number.isSafeInteger(end)
        || end < start || end > length || (format === "bedgraph" && (end === start || !value.trim() || !Number.isFinite(Number(value))))) {
        throw new ViewerFileError("trackInvalid");
    }
    if (format === "bed" && columns.length >= 10) {
        const count = Number(columns[9]);
        const sizes = (columns[10] ?? "").replace(/,$/, "").split(",");
        const starts = (columns[11] ?? "").replace(/,$/, "").split(",");
        if (!/^\d+$/.test(columns[9] ?? "") || !Number.isSafeInteger(count) || count < 1 || sizes.length !== count || starts.length !== count
            || sizes.some((size, index) => {
                const offset = starts[index] ?? "";
                return !/^\d+$/.test(size) || !/^\d+$/.test(offset) || !Number.isSafeInteger(Number(size))
                    || !Number.isSafeInteger(Number(offset)) || Number(size) < 1 || Number(offset) + Number(size) > end - start;
            })) {
            throw new ViewerFileError("trackInvalid");
        }
    }
}

export type ViewerTrackConfig<Source> = (Omit<LocalTrack<Source>, "format"> & {
    format: LocalTrack<Source>["format"];
    delimiter?: string;
    decode?: (columns: string[]) => { chr: string; start: number; end: number; value?: number; name?: string } | undefined;
});

/** igv calls this decoder only on the indexed blocks it reads, including after panning. */
export function validatedBedGraphTrack<Source>(track: LocalTrack<Source>, report: (error: ViewerFileError) => void): ViewerTrackConfig<Source> {
    if (track.format === "rsid-tab") {
        return {
            ...track,
            format: "bed",
            delimiter: "\t",
            decode(columns) {
                const contig = columns[0] ?? "";
                const rawPos = columns[1] ?? "";
                if (columns.length < 3 || contig.startsWith("#") || !/^\d+$/.test(rawPos)) return undefined;
                const pos = Number(rawPos);
                if (!Number.isSafeInteger(pos) || pos < 1) return undefined;
                const name = (columns[2] ?? "").trim();
                return { chr: contig, start: pos - 1, end: pos, name };
            },
        };
    }
    if (track.format !== "bedgraph" || !track.referenceLengths) return track;
    const reference = new Map(Object.entries(track.referenceLengths));
    return {
        ...track,
        decode(columns) {
            try {
                validateTrackRecord(columns, "bedgraph", reference);
            } catch (error) {
                if (!(error instanceof ViewerFileError)) throw error;
                report(new ViewerFileError(error.code, track.name));
                return undefined;
            }
            return { chr: columns[0] ?? "", start: Number(columns[1]), end: Number(columns[2]), value: Number(columns[3]) };
        },
    };
}
