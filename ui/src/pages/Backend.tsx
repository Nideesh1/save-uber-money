import { AgentScene, THEMES, THEME_INFO, type Theme } from "agentglow";
import { useState } from "react";

const GLOW = (import.meta.env?.VITE_AGENTGLOW as string | undefined) ?? "http://localhost:8103";
const KEY = "nycrides.glowTheme";

function initial(): Theme {
  try {
    const v = localStorage.getItem(KEY);
    if (v && (THEMES as readonly string[]).includes(v)) return v as Theme;
  } catch { /* storage blocked */ }
  return "neural";
}

export default function Backend() {
  const [theme, setTheme] = useState<Theme>(initial);
  const pick = (t: Theme) => {
    setTheme(t);
    try { localStorage.setItem(KEY, t); } catch { /* storage blocked */ }
  };
  return (
    <div className="scene">
      <AgentScene theme={theme} source={GLOW} hud style={{ position: "absolute", inset: 0 }} />
      <div className="themes" role="radiogroup" aria-label="scene theme">
        {THEMES.map((t) => (
          <button key={t} role="radio" aria-checked={t === theme} className={t === theme ? "on" : ""} title={THEME_INFO[t]?.tagline} onClick={() => pick(t)}>{THEME_INFO[t]?.name ?? t}</button>
        ))}
      </div>
    </div>
  );
}
