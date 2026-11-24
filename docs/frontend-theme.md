# Warm cream and burgundy

The site uses an editorial club palette chosen by the user: warm cream pages,
burgundy actions and navigation, blush selection states, warm gold on dark
panels, and ink text. The existing typography, layouts, native controls and
motion preference remain shared across the product.

| Role | Token | Color |
| --- | --- | --- |
| Page | `--as-bg` | `#f7f1e7` |
| Panel / input | `--as-surface` | `#fffdf8` |
| Main text | `--as-ink` | `#33252b` |
| Secondary text | `--as-muted` | `#6f5b60` |
| Primary action / link | `--as-primary` | `#7b2943` |
| Primary hover | `--as-primary-hover` | `#5d1d34` |
| Dark navigation / feature panel | `--as-dark` | `#3f1727` |
| Dark raised surface | `--as-dark-raised` | `#5a2339` |
| Text on dark surfaces | `--as-on-dark` | `#fff7ee` |
| Gold accent | `--as-lime` (legacy alias) | `#efc995` |
| Selected surface | `--as-tint` | `#f1e1e2` |
| Keyboard focus on light | `--as-focus` | `#9e3758` |
| Input outline | `--as-control-border` | `#a58b7e` |

`static/css/frontend.css` owns the product tokens. The scoped public, login,
workshop, competition, social and game styles use them directly. Legacy
`design-system.css` / `style.css` tokens also follow the warm palette. Brand
colors in dynamic cards, charts, the model library, online lobby and match
setup have been updated, including mobile navigation and dialogs. Decorative
SVG surrounds and shirts match the palette. Green remains meaningful in small
success states and on the actual football turf; gameplay team colors remain
distinct.

The login page presents animated soccer artwork. Decorative passing motion,
board drift, a grid and glow use CSS; the form stays stationary. The Motion
switch, reduced-motion preference and visibility lifecycle control these
effects without a JavaScript frame loop. Verify with
`python tools/browser/verify_cinematic.py` and `python tools/browser/verify_login.py`.

Soccer passes and board drift use a shared symmetric easing curve. The
background grid moves one complete tile under a stationary mask, so its wrap
does not jump in brightness.
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
Gold is used on dark surfaces; dark ink is used for small text on gold cards.
Game scores use solid text fill so a legacy gradient cannot hide a score
against the burgundy HUD.

Run `python tools/browser/verify_warm_theme.py` for desktop/mobile route,
palette, keyboard-focus, game/dialog and sampled contrast verification.
Screenshots and its report are stored under the system temporary directory in
`agent-soccer-warm-theme`. This is a targeted check, not a claim of a complete
accessibility audit.
