export const linkedMoveFolderFormat = "Cannot move a folder from or to the link “%{link}” (target: “%{target}”). Move the target yourself and update the setting, or choose a destination that is not a link.";
export const linkedMoveTargetUnavailable = "unavailable";

export function formatLinkedMoveFolder(
    { link, target }: { link: string; target: string | null },
    format = linkedMoveFolderFormat,
    unavailable = linkedMoveTargetUnavailable,
): string {
    return format.replace(/%\{(link|target)\}/g, (_, key) => key === "link" ? link : target ?? unavailable);
}
