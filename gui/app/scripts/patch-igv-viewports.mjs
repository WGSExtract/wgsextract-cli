/** igv 3.8.10 detaches search viewports without disposing their ruler observers.
 * Those observers retain old reference frames, decoded reads and canvases (#1158).
 * Patch the pinned distribution at build time; fail closed when upstream changes.
 */
export function disposeDiscardedViewports(code) {
  const before = "removeViewportsFromColumnContainer(){for(let t of this.viewports)t.viewportElement.remove()}";
  // Do not clear viewport fields with viewport.dispose(): a previous search may
  // still be awaiting a read. Unsubscribe its external roots, then let that
  // operation finish safely and GC reclaim the detached viewport graph.
  const after = "removeViewportsFromColumnContainer(){for(let t of this.viewports){t.rulerSweeper?.dispose();t.viewportElement.remove()}}";
  if (code.split(before).length !== 2) {
    throw new Error("Review the igv viewport disposal patch before changing the pinned distribution.");
  }
  return code.replace(before, after);
}
