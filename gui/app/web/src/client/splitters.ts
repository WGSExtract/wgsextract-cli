import { clamp } from "./dom.js";
import { state } from "./state.js";

export function bindSplitters(app: HTMLElement) {
    if (!state.isSidebarVisible) {
        return;
    }
    app.querySelector("[data-sidebar-resizer]")?.addEventListener("pointerdown", (event) => {
        const pointerEvent = event as PointerEvent;
        event.preventDefault();
        const startX = pointerEvent.clientX;
        const startWidth = state.sidebarWidth;
        document.body.classList.add("resizing-sidebar");
        const move = (moveEvent: PointerEvent) => {
            state.sidebarWidth = clamp(startWidth + moveEvent.clientX - startX, 160, 420);
            app.style.setProperty("--sidebar-width", `${state.sidebarWidth}px`);
        };
        const up = () => {
            localStorage.setItem("wgsextractGui.sidebarWidth", String(Math.round(state.sidebarWidth)));
            document.body.classList.remove("resizing-sidebar");
            window.removeEventListener("pointermove", move);
            window.removeEventListener("pointerup", up);
        };
        window.addEventListener("pointermove", move);
        window.addEventListener("pointerup", up, { once: true });
    });
    app.querySelector("[data-terminal-resizer]")?.addEventListener("pointerdown", (event) => {
        const pointerEvent = event as PointerEvent;
        event.preventDefault();
        const startY = pointerEvent.clientY;
        const startHeight = state.terminalHeight;
        document.body.classList.add("resizing-terminal");
        const move = (moveEvent: PointerEvent) => {
            state.terminalHeight = clamp(startHeight - (moveEvent.clientY - startY), 96, Math.max(96, window.innerHeight - 260));
            app.style.setProperty("--terminal-height", `${state.terminalHeight}px`);
        };
        const up = () => {
            localStorage.setItem("wgsextractGui.terminalHeight", String(Math.round(state.terminalHeight)));
            document.body.classList.remove("resizing-terminal");
            window.removeEventListener("pointermove", move);
            window.removeEventListener("pointerup", up);
        };
        window.addEventListener("pointermove", move);
        window.addEventListener("pointerup", up, { once: true });
    });
}
