import { createModalFocusBinder } from "./modal-focus.js";

export const bindPrerequisitesFocus = createModalFocusBinder(
    "[data-prerequisites-dialog]",
    "[data-prerequisites-install], [data-prerequisites-continue]",
);
