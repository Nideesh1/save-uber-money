import { AgentScene } from "agentglow";

const GLOW = (import.meta.env?.VITE_AGENTGLOW as string | undefined) ?? "http://localhost:8103";

// the theme picker lives in the AgentGlow HUD (click the theme title)
export default function Backend() {
  return (
    <div className="scene">
      <AgentScene theme="neural" source={GLOW} hud style={{ position: "absolute", inset: 0 }} />
    </div>
  );
}
