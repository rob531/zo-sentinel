# MCPRisky style guidelines v2

2026-11-27 · living copy: https://claude.ai/artifact/DpuTYBGwDgs5ndLvrVnQr1

This is the reference the designer lane (`zo-fleet-tools/design_audit.py`) scores its subjective
dimensions S1 (visual craft and brand cohesion) and S2 (cognitive ergonomics and user journey) against.

## Summary

MCPRisky looks dated because every word on every page is set in a monospace font, at 30 different ad-hoc
sizes, with cyan glow effects and hard-coded colours that bypass the token file. The palette itself
(obsidian + cyan) is strong and worth keeping.

v2 keeps the dark obsidian-cyan identity but moves to a calm "security intelligence" look: a sans-serif UI
font with monospace reserved for data, one type scale, one radius scale, no glow, and a single risk colour
scale used identically everywhere.

The lane's deterministic audit scores the UI 96.8/100 (static run 2026-11-27) because S1/S2 have never been
scored and D4 only checks that canonical tokens are present.

## What the audit found

| Area | Now | Why it reads as dated |
| --- | --- | --- |
| Typeface | JetBrains Mono for all text, body copy included | Prose in monospace reads as a terminal and is slower to read |
| Type sizes | 30 distinct font sizes across the 10 surfaces, px and rem mixed | No rhythm; headings and labels barely differ |
| Colour | 6 extra hard-coded background shades (#0d0a16, #0b0814, #15101f ...), teal gradient button (#1b9fb3 to #0e7c8c), 21 ad-hoc rgba values | Subtly different blacks; gradient button is a 2015 pattern |
| Effects | Cyan text-shadow glow on logo; radial purple glow behind every page | Neon glow is the main "cyber" cliché |
| Shape | 9 border radii (6, 7, 8, 9, 10, 11, 12, 16, 99px) | Cards and buttons never quite match |
| Copy styling | `// comment` prefixes, ALL-CAPS buttons | Developer-tool voice on a public page |
| Navigation | /scan and /perspectives header items carry an unexplained trailing mark | Reads as truncated |
| Error states | Public /perspectives shows raw `401 {"detail":"Authentication required"}` and an empty table | Raw API errors on a public page |
| Landing | One 580px card with a scrolling legal notice; no hero or product preview | First screen is a disclaimer, not the product |

## Principles

1. **Data is the hero.** Risk verdict and evidence are the brightest things on screen; chrome and legal text recede.
2. **Calm, not cyber.** No glow, neon gradients or scan-lines.
3. **One colour means one thing.** Cyan = interaction. Risk colours = risk only.
4. **Sans for reading, mono for data.**
5. **Honest states.** Designed loading, empty, error and signed-out states in plain language; `UNKNOWN` is first-class.
6. **Tokens or nothing.** No raw hex, px font size or radius in a view file.

## Colour tokens

All text and risk colours pass WCAG AA (4.5:1) on bg, surface-1 and surface-2 (computed with `design_audit.contrast_ratio`).

| Token | Value | Use | Contrast bg / surface-2 |
| --- | --- | --- | --- |
| `--bg` | #0A0910 | Page background, flat | |
| `--surface-1` | #12111A | Cards, header, panels | |
| `--surface-2` | #1A1824 | Hover rows, inputs, menus | |
| `--border` | #2A2838 | 1px dividers, card outlines | |
| `--text` | #ECEBF2 | Primary text | 16.7 / 14.8 |
| `--text-2` | #A8A5B8 | Secondary text, labels | 8.3 / 7.3 |
| `--text-3` | #8B889F | Captions, timestamps, placeholders | 5.8 / 5.1 |
| `--accent` | #3DD9F5 | Links, focus ring, primary button | 11.7 / 10.4 |
| `--on-accent` | #06141A | Text on primary button | 11.1 on accent |
| `--risk-low` | #4ADE80 | LOW, TRUSTED_* | 11.4 / 10.0 |
| `--risk-medium` | #FACC15 | MEDIUM, CAUTION_LIMITED | 12.9 / 11.4 |
| `--risk-high` | #FB923C | HIGH, HIGH_RISK_ISOLATED | 8.8 / 7.7 |
| `--risk-critical` | #FF5C7A | CRITICAL, KNOWN_THREAT | 6.7 / 5.9 |
| `--risk-unknown` | #9CA3AF | UNKNOWN, unassessed, INSUFFICIENT | 7.8 / 6.9 |

- Risk badges: risk colour text + border on a 12% tint, never a solid fill.
- Risk is never colour-only: badge carries the word; tables add a 4-step bar glyph.
- Retire `--gold` and the teal gradient. Map legacy aliases (`--LOW`, `--hi`, `--med`, `--dim`, `--cyan`) for one release, then drop.
- Light mode out of scope; names allow adding it later.

## Typography

Inter for UI text; JetBrains Mono only for data. Google Fonts, `display=swap`; Inter 400/500/600, Mono 400/600.

| Token | Size / line height | Weight | Font | Use |
| --- | --- | --- | --- | --- |
| `--fs-display` | 32 / 40px | 600 | Inter | Landing hero only |
| `--fs-h1` | 24 / 32px | 600 | Inter | Page title |
| `--fs-h2` | 18 / 26px | 600 | Inter | Section and card titles |
| `--fs-body` | 14 / 22px | 400 | Inter | Body, table cells, fields |
| `--fs-small` | 13 / 20px | 500 | Inter | Labels, nav, buttons, badges |
| `--fs-caption` | 12 / 16px | 400 | Inter | Timestamps, footnotes, build badge |
| `--fs-mono` | 13 / 20px | 400 | JetBrains Mono | Server ids, scores, hashes, config |

- Mono only where a value is copied, compared, or column-aligned (`tabular-nums`, right-aligned scores).
- Sentence case for buttons and headings. Uppercase only for caption-size eyebrows, `letter-spacing: .06em`.
- No `// comment` prefix in user-facing copy.
- Wordmark: "MCP" `--text`, "Risky" `--accent`, Inter 600, no glow.

## Spacing, radius, elevation

| Token | Value | Use |
| --- | --- | --- |
| `--space-1..8` | 4, 8, 12, 16, 24, 32, 48, 64px | All padding, gaps, margins |
| `--radius-sm` | 6px | Buttons, inputs, badges, menu items |
| `--radius-md` | 10px | Cards, panels, menus, modals |
| `--radius-pill` | 999px | Filter chips, toggles |
| `--shadow-pop` | 0 8px 24px rgba(0,0,0,.45) | Menus and modals only |

Content max width 1200px, gutters 24px (16px under 640px); header 56px sticky on `--surface-1`;
card padding 16px; table cells 10px x 12px; breakpoints 1440 / 768 / 390.

## Components

| Component | Spec |
| --- | --- |
| Header | Wordmark left; nav `--fs-small` in `--text-2`; active `--text` + 2px `--accent` underline; overflow in one "More" menu with chevron; no trailing marks |
| Primary button | Solid `--accent`, `--on-accent` text, `--radius-sm`, 36px, 600, sentence case; disabled 40% opacity, no grayscale; no gradient |
| Secondary button | Transparent, 1px `--border`, `--text`; hover `--surface-2` |
| Risk badge | `--radius-sm`, 20px, `--fs-small` uppercase, colour text+border on 12% tint; order CRITICAL, HIGH, MEDIUM, LOW, UNKNOWN |
| Score | Mono tabular, right-aligned, 48px thin bar beneath tinted by tier |
| Data table | Sticky header `--text-2`; rows 40px; 1px dividers, no zebra; hover `--surface-2`; whole row clickable; CRITICAL rows 3px `--risk-critical` left edge |
| Card / stat tile | `--surface-1`, 1px border, `--radius-md`, 16px; caption label above 24px number |
| Filter chip | `--radius-pill`, `--surface-2`, removable x; active outlined `--accent` |
| Notice | Collapsed to one sentence + "Read the full notice"; no inner scroll box |
| Empty state | Why it is empty + one action |
| Error state | Plain sentence, never raw status/JSON; Retry; details in a disclosure; 401 on a public page = sign-in prompt |
| Loading | Content-shaped skeletons (keep `chrome.css` ones) |
| Build badge | `--fs-caption`, `--text-3`, no border |

## Rollout

1. Tokens and type: rewrite `app/static/tokens.css` (legacy aliases mapped), add Inter, body to Inter, `.mono` utility.
2. Chrome and components in `chrome.css`; remove logo glow and radial page glow.
3. Page sweep: tokens replace hard-coded hex / px sizes / radii in the 9 view files (landing, /app, /scan, /perspectives first).
4. Landing redesign: hero, live scored-count stat, search/Scan CTA, product screenshot, notice collapsed beside the CTA.

Page fixes: /perspectives signed-out 401 -> sign-in prompt (or drop from public footer); remove nav trailing marks; sentence-case landing button.

Designer lane (`design_audit.py`): tighten D4 to count hard-coded hex / px font sizes / radii outside `tokens.css` against a zero budget;
update `CANONICAL_TOKENS` after step 1; score S1/S2 against this document with a screenshot pass at the three breakpoints.
