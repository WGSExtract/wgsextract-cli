export type ScrollSnapshot = {
    key: string;
    left: number;
    top: number;
};

export type ScrollableElement = {
    scrollLeft: number;
    scrollTop: number;
};

export function captureScrollState(element: ScrollableElement | null | undefined, key: string | null | undefined): ScrollSnapshot | null {
    if (!element || !key) {
        return null;
    }
    return {
        key,
        left: element.scrollLeft,
        top: element.scrollTop,
    };
}

export function restoreScrollState(element: ScrollableElement | null | undefined, snapshot: ScrollSnapshot | null, key: string | null | undefined) {
    if (!element || !snapshot || snapshot.key !== key) {
        return false;
    }
    element.scrollLeft = snapshot.left;
    element.scrollTop = snapshot.top;
    return true;
}

export type KeyedScrollRoot<Element extends ScrollableElement & { dataset: { scrollKey?: string } }> = {
    querySelectorAll(selector: string): ArrayLike<Element>;
};

/** Saves the scroll position of every element marked `data-scroll-key` (a long table that scrolls on its own). */
export function captureKeyedScrollStates<Element extends ScrollableElement & { dataset: { scrollKey?: string } }>(root: KeyedScrollRoot<Element>): ScrollSnapshot[] {
    return Array.from(root.querySelectorAll("[data-scroll-key]"))
        .map((element) => captureScrollState(element, element.dataset.scrollKey))
        .filter((snapshot): snapshot is ScrollSnapshot => snapshot !== null);
}

/** Puts each re-rendered `data-scroll-key` element back where its predecessor with the same key was scrolled. */
export function restoreKeyedScrollStates<Element extends ScrollableElement & { dataset: { scrollKey?: string } }>(root: KeyedScrollRoot<Element>, snapshots: ScrollSnapshot[]): void {
    const byKey = new Map(snapshots.map((snapshot) => [snapshot.key, snapshot]));
    for (const element of Array.from(root.querySelectorAll("[data-scroll-key]"))) {
        const key = element.dataset.scrollKey;
        restoreScrollState(element, key ? byKey.get(key) ?? null : null, key);
    }
}
