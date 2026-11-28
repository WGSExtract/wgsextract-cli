import { escapeAttribute, escapeHTML } from "../dom.js";
import { localizedStatus } from "../model.js";
import { state } from "../state.js";
import type { LibrarySort, RowColumnSpec, RowSpec } from "../../../../shared/types.js";

const sizeUnits: Record<string, number> = { b: 1, kb: 1e3, mb: 1e6, gb: 1e9, tb: 1e12, kib: 1024, mib: 1024 ** 2, gib: 1024 ** 3, tib: 1024 ** 4 };
const collator = new Intl.Collator(undefined, { numeric: true, sensitivity: "base" });

/** What a cell shows, as text: the name column shows the row's title, and the status column its localized status. */
function cellText(row: RowSpec, column: RowColumnSpec): string {
    if (column.id === "name") return String(row.title ?? row.values?.name ?? row.id ?? "");
    if (column.id === "status") return localizedStatus(row.status ?? row.values?.status ?? "");
    return String(row.values?.[column.id] ?? "");
}

/** Sizes such as "3.1 GB" compare by bytes; other text compares naturally, so "v10" follows "v9". */
function sizeInBytes(text: string): number | undefined {
    const match = /^([\d.]+)\s*([kmgt]i?b|b)$/i.exec(text.trim());
    const unit = match ? sizeUnits[match[2]?.toLowerCase() ?? ""] : undefined;
    return match && unit ? Number(match[1]) * unit : undefined;
}

function compareCells(left: string, right: string): number {
    const leftSize = sizeInBytes(left);
    const rightSize = sizeInBytes(right);
    if (leftSize !== undefined && rightSize !== undefined) return leftSize - rightSize;
    return collator.compare(left, right);
}

/** The rows in the table's sort order; without a sort, or for an unknown column, the data source's own order. */
export function sortedRows(rows: RowSpec[], columns: RowColumnSpec[], sort: LibrarySort | undefined): RowSpec[] {
    const column = sort ? columns.find((candidate) => candidate.id === sort.column) : undefined;
    if (!sort || !column) return rows;
    const keyed = rows.map((row, index) => ({ row, index, text: cellText(row, column).trim() }));
    keyed.sort((left, right) => {
        // Empty cells stay last either way; ties keep the original order.
        if (!left.text || !right.text) return Number(!left.text) - Number(!right.text) || left.index - right.index;
        const order = compareCells(left.text, right.text);
        return (sort.descending ? -order : order) || left.index - right.index;
    });
    return keyed.map((entry) => entry.row);
}

/** A column heading that sorts its table when clicked, marked ascending or descending while it does. */
export function renderSortableHeading(key: string, column: RowColumnSpec, sort: LibrarySort | undefined) {
    const active = sort?.column === column.id;
    const direction = active ? (sort.descending ? "descending" : "ascending") : "none";
    const arrow = active ? (sort.descending ? "▼" : "▲") : "";
    const title = String(column.title ?? "");
    const tooltip = state.labels.librarySortColumnTooltip ?? "";
    return `<th aria-sort="${direction}"><button type="button" class="column-sort${active ? " active" : ""}" data-library-sort="${escapeAttribute(key)}" data-library-sort-column="${escapeAttribute(String(column.id ?? ""))}" title="${escapeAttribute(tooltip)}">${escapeHTML(title)}<span class="column-sort-arrow" aria-hidden="true">${arrow}</span></button></th>`;
}

/** Default Sort, beside the filter: clears the column sort and restores the table's original order. */
export function renderDefaultSortButton(key: string, sort: LibrarySort | undefined) {
    return `<button type="button" class="secondary-button" data-library-sort-reset="${escapeAttribute(key)}" title="${escapeAttribute(state.labels.librarySortDefaultTooltip ?? "")}" ${sort ? "" : "disabled"}>${escapeHTML(state.labels.librarySortDefaultTitle ?? "Default Sort")}</button>`;
}

/** Clicking the sorted column reverses it; another column starts ascending. */
export function nextLibrarySort(current: LibrarySort | undefined, column: string): LibrarySort {
    return current?.column === column ? { column, descending: !current.descending } : { column, descending: false };
}
