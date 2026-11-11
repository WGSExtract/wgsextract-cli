import { captureFocus, restoreFocus, type FocusSnapshot } from "./focus-state.js";

// The control that had focus when the prerequisites dialog opened (the action's button), given focus back once it closes.
let opener: FocusSnapshot | null = null;

/**
 * Keeps keyboard focus in the prerequisites dialog while it is open, as a modal dialog should: after each render
 * it moves focus into the dialog (its primary button) if a re-render left it behind the dialog, Tab and Shift+Tab
 * cycle through the dialog's buttons, and Escape cancels. Once the dialog is gone, focus returns to the control
 * that opened it.
 */
export function bindPrerequisitesFocus(app: HTMLElement, cancel: () => void) {
    const dialog = app.querySelector<HTMLElement>("[data-prerequisites-dialog]");
    if (!dialog) {
        if (opener) {
            const snapshot = opener;
            opener = null;
            restoreFocus(app, snapshot);
        }
        return;
    }
    if (!dialog.contains(globalThis.document?.activeElement ?? null)) {
        opener ??= captureFocus(app);
        const primary = dialog.querySelector<HTMLElement>("[data-prerequisites-install]")
            ?? dialog.querySelector<HTMLElement>("[data-prerequisites-continue]")
            ?? dialog;
        primary.focus({ preventScroll: true });
    }
    dialog.addEventListener("keydown", (event) => {
        if (event.key === "Escape") {
            event.preventDefault();
            cancel();
            return;
        }
        if (event.key !== "Tab") {
            return;
        }
        const buttons = [...dialog.querySelectorAll<HTMLElement>("button:not([disabled])")];
        const first = buttons[0];
        const last = buttons[buttons.length - 1];
        if (!first || !last) {
            event.preventDefault();
            return;
        }
        const active = globalThis.document?.activeElement;
        if (event.shiftKey && (active === first || active === dialog)) {
            event.preventDefault();
            last.focus();
        }
        else if (!event.shiftKey && active === last) {
            event.preventDefault();
            first.focus();
        }
    });
}
