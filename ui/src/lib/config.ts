export const API: string = (import.meta.env?.VITE_API as string | undefined) ?? "http://localhost:8420";

/** 6 -> "6am", 22.5 -> "10:30pm", 0 -> "12am". */
export function hourLabel(h: number): string {
  const whole = Math.floor(h), min = Math.round((h - whole) * 60);
  const hh = ((whole % 24) + 24) % 24;
  const base = hh % 12 === 0 ? 12 : hh % 12;
  return `${base}${min ? `:${String(min).padStart(2, "0")}` : ""}${hh < 12 ? "am" : "pm"}`;
}
