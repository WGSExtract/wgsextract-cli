export type PrimitiveValue = string | number | boolean | null;
export type StateValue = PrimitiveValue | undefined;
export type StringMap = Record<string, string>;
export type ValueMap = Record<string, StateValue>;
export type LooseRecord = Record<string, unknown>;
