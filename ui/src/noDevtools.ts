// Suppress the OpenUI dev inspector widget (dev only) unless ?devtools is in the URL. Must be imported first.
if (typeof location !== "undefined" && !new URLSearchParams(location.search).has("devtools")) {
  (globalThis as Record<symbol, unknown>)[Symbol.for("openui.devtools.autoMount")] = true;
}
export {};
