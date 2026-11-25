# Soft white, midnight navy and blue

The site uses soft white pages, midnight navy navigation, bright blue actions,
pale blue selection states and slate text. This replaces the previous dark red
palette at the user's request. Typography, native controls and the motion
preference remain shared across the product.

The clean interface uses a compact navigation bar, short page introductions,
flat surfaces and more readable body text. Play, Soccer AI, Compete, Club and
Learn use grouped destination lists with one primary action. The locker room
puts the player build beside its edit action. Profile metrics share one grid;
workshop tabs and competition summaries use light separators. The full-width
match layout remains the focus of the game page.

Public pages use a single soccer illustration and fewer promotional panels.
Sign-in shows a stationary form beside the illustration on desktop, and the
form alone on mobile. Review captures are in `docs/previews/clean-interface/`.

| Role | Token | Color |
| --- | --- | --- |
| Page | `--as-bg` | `#f5f7fb` |
| Panel / input | `--as-surface` | `#ffffff` |
| Soft surface | `--as-soft` | `#edf2f8` |
| Main text | `--as-ink` | `#17253d` |
| Secondary text | `--as-muted` | `#52627a` |
| Separator | `--as-line` | `#d6dfeb` |
| Primary action / link | `--as-primary` | `#2459d3` |
| Primary hover | `--as-primary-hover` | `#1c46ad` |
| Secondary accent | `--as-accent` | `#1b5b9b` |
| Dark navigation / feature panel | `--as-dark` | `#142644` |
| Dark raised surface | `--as-dark-raised` | `#203b60` |
| Dark surface border | `--as-dark-line` | `#526d91` |
| Text on dark surfaces | `--as-on-dark` | `#f8fbff` |
| Secondary text on dark | `--as-on-dark-muted` | `#cad8eb` |
| Sky accent | `--as-lime` (legacy alias) | `#a9d9ff` |
| Selected surface | `--as-tint` | `#e7efff` |
| Keyboard focus on light | `--as-focus` | `#2459d3` |
| Input outline | `--as-control-border` | `#8191a9` |

`static/css/frontend.css` owns the product tokens. The scoped public, login,
workshop, competition, social and game styles use them directly. Legacy
`design-system.css` / `style.css` tokens also follow the blue palette. Brand
colors in dynamic cards, charts, the model library, online lobby and match
setup have been updated, including mobile navigation and dialogs. Decorative
SVG surrounds and shirts match the palette. Green remains meaningful in small
success states and on the actual football turf; gameplay team colors remain
distinct.

The login page presents animated soccer artwork. Decorative passing motion
and board drift use CSS; the form stays stationary. The Motion
switch, reduced-motion preference and visibility lifecycle control these
effects without a JavaScript frame loop. Verify with
`python tools/browser/verify_cinematic.py` and `python tools/browser/verify_login.py`.

Soccer passes and board drift use a shared symmetric easing curve. Decorative
background grids, glow layers and the pulsing sign-in badge have been removed.
Hidden mobile illustrations stay asleep. Destination hover effects are subtle
and do not tilt the text.
Hover transitions cover both entry and release, and delayed entrances apply
their starting state during the stagger to avoid a flash before moving.

This follows [web.dev's animation performance guidance](https://web.dev/articles/animations-guide)
to animate transforms and opacity, and [MDN's per-keyframe easing guidance](https://developer.mozilla.org/en-US/docs/Web/CSS/Reference/Properties/animation-timing-function)
to control the flight's rise and fall independently. No blanket `will-change`
layer hints are added; [MDN cautions against using these without evidence](https://developer.mozilla.org/en-US/docs/Web/CSS/Reference/Properties/will-change).
The motion verifier checks loop seams, stable
layout and pause controls. Browser frame timings are diagnostics for the test
machine, not a frame-rate guarantee on every device.
Chromium counts internal SVG geometry updates as layout work; the report keeps
those counters and durations visible alongside actual page-shift measurements.

## Research and checks

The design separates primary, surface and on-surface roles, following
[Material's color-role approach](https://developer.android.com/develop/ui/compose/designsystems/material3#color-scheme).
Shared tokens use [CSS custom properties](https://developer.mozilla.org/en-US/docs/Web/CSS/Guides/Cascading_variables/Using_custom_properties)
so a future palette change does not require editing repeated hardcoded colors.

Text targets follow [WCAG 2.2 contrast guidance](https://www.w3.org/WAI/WCAG22/Understanding/contrast-minimum.html):
4.5:1 for normal text and 3:1 for large text. Meaningful control boundaries and
focus indicators target 3:1 under
[non-text contrast guidance](https://www.w3.org/WAI/WCAG22/Understanding/non-text-contrast.html).
Sky blue is used on dark surfaces; dark ink is used for small text on pale blue cards.
Game scores use solid text fill so a legacy gradient cannot hide a score
against the navy HUD.

Run `python tools/browser/verify_warm_theme.py` for desktop/mobile route,
palette, keyboard-focus, game/dialog and sampled contrast verification.
The script retains its original filename for compatibility and checks the
current blue palette, including exact navy navigation and white dialog surfaces.
Screenshots and its report are stored under the system temporary directory in
`agent-soccer-warm-theme`. This is a targeted check, not a claim of a complete
accessibility audit.

`verify_frontend.py`, `verify_frontend_workshop.py` and
`verify_frontend_dialogs.py` exercise navigation, account forms, the AI editor,
customization and dialogs. `verify_match_focus.py` covers the large pitch,
settings disclosures, fullscreen and controls. All use isolated local data.
