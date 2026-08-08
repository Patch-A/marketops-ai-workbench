# MarketOps Visual System

## Scene

An experienced marketer is reviewing a campaign brief beside a second monitor in a bright office, switching between evidence, decisions, and a short list of next actions. The UI should reduce cognitive noise rather than perform intelligence.

## Strategy

Restrained product palette. Use near-white and cool neutral surfaces, ink for reading, blue-green for approved or actionable states, and coral only for risk, missing evidence, or human attention. No gradients, decorative blobs, glass panels, or purple AI glow.

## Tokens

- Ink: `oklch(0.24 0.025 255)`
- Muted ink: `oklch(0.48 0.035 255)`
- Canvas: `oklch(0.975 0.012 230)`
- Surface: `oklch(1 0 0)`
- Sidebar: `oklch(0.19 0.035 255)`
- Line: `oklch(0.89 0.025 235)`
- Action: `oklch(0.47 0.13 178)`
- Action tint: `oklch(0.93 0.055 178)`
- Attention: `oklch(0.62 0.16 32)`
- Attention tint: `oklch(0.95 0.055 32)`
- Information: `oklch(0.55 0.12 230)`

Use system sans for product UI and a compact monospace face only for sources, timestamps, and IDs. Use 8px as the base spacing unit. Cards are rectangular with 8px radius; sections use open layout and borders instead of nested floating cards.

## Interaction

Most transitions are 180ms ease-out. Motion communicates status changes only. Never hide content behind an entrance animation. Respect `prefers-reduced-motion`.
