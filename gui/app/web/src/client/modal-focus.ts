import { captureFocus, restoreFocus, type FocusSnapshot } from "./focus-state.js";

/**
 * Keeps keyboard focus in a dialog while it is open: after each render
 * it moves focus into the dialog (its primary button) if a re-render left it behind the dialog, Tab and Shift+Tab
 * cycle through the dialog's buttons, and Escape cancels. Once the dialog is gone, focus returns to the control
 * that opened it.
 */
export function createModalFocusBinder(selector: string, primarySelector: string, restoreEdit = true) {
    let opener: FocusSnapshot | null = null;
    return function bindModalFocus(app: HTMLElement, cancel: () => void) {
        const dialog = app.querySelector<HTMLElement>(selector);
        if (!dialog) {
            if (opener) {
                const snapshot = opener;
                opener = null;
                restoreFocus(app, snapshot);
            }
            return;
        }
        if (!dialog.contains(globalThis.document?.activeElement ?? null)) {
            const captured = captureFocus(app);
            if (!opener && captured) {
                opener = restoreEdit ? captured : { selector: captured.selector };
            }
            const primary = dialog.querySelector<HTMLElement>(primarySelector) ?? dialog;
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
    };
}
