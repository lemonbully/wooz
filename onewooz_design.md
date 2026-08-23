# OneWooz — Marketing Design System

**Direction:** "Palantir" — a stark, engineered treatment for the entire public
site, inspired by palantir.com. Near-white paper and near-black ink, hairline
rules on everything, monospace metadata, section numbering, corner-bracket
framing, oversized grotesque type, and squared geometry.

> **No glass, no gradients, no glow.** Depth is *structural* — it comes from
> rules and type, never from effects.

**Source of truth:** `onewooz/static/css/onewooz-pal.css`. Every rule is scoped
under `.pal`, which wraps the marketing `<body>` in `templates/web/_shell.html`.
The app dashboard, admin and auth chrome keep `onewooz.css` untouched and are
unaffected by anything in this document.

---

## 1. Mood board

The whole site should read as a **technical document / datasheet**, not a
brochure. Reach for these words when in doubt:

| Feeling | Rendered as |
|---|---|
| Engineered | Hairline rules framing everything; a 6-column blueprint grid faint behind sections |
| Precise | Monospace metadata, section numbers (`01 —`), tabular figures, corner brackets |
| Stark | Cool near-white paper (`#f4f4f2`) against near-black ink (`#0a0a0b`); extremes, no mid-greys as backgrounds |
| Restrained | One accent (`#4b2bf5`), used as a tick / an active word / a link — never as fill |
| Squared | **Radius 0 everywhere.** No rounded corners on cards, buttons, inputs, panels |
| Unhurried | Slow, heavy-settle entrance motion (~1050ms, expo-out) with a focus-in blur |
| Schematic | Illustration is line art, terminal feeds, pipelines, dashboards — "markup", not cartoon |

**Anti-patterns (do not do):** drop shadows for elevation, gradient fills,
glassmorphism/blur panels (except the nav's subtle backdrop), rounded corners,
soft pastel palettes, decorative photography in colour (imagery is
grayscale + contrast until hover), more than one accent hue.

**Reference registers:** Mobbin restraint + Templafy section rhythm +
Palantir engineered starkness.

---

## 2. Color

All colors are CSS custom properties declared on `.pal`. Two are inherited from
the base layer (`onewooz.css` `:root`).

### Ground — paper & ink (pushed to extremes)

| Token | Value | Role |
|---|---|---|
| `--pal-paper` | `#f4f4f2` | Page background. Cool near-white. |
| `--pal-paper-2` | `#ececea` | Secondary surface / hover fills / code blocks. |
| `--pal-panel` | `#ffffff` | Pure-white raised panel (cards, store mock, inputs). |
| `--pal-ink` | `#0a0a0b` | Primary text; inverted (ink-background) sections; solid buttons. |
| `--pal-ink-2` | `#26262a` | Secondary text (leads, prose body). |
| `--pal-muted` | `#66666b` | Muted text (descriptions, labels, metadata). |
| `--pal-faint` | `#9a9aa0` | Faintest text (footer meta, logo row, placeholders). |

### Lines — the signature (everything is ruled)

| Token | Value | Role |
|---|---|---|
| `--pal-line` | `rgb(10 10 11 / 0.16)` | Standard hairline rule on paper. |
| `--pal-line-soft` | `rgb(10 10 11 / 0.07)` | Faint rule — blueprint grid, sub-dividers. |
| `--pal-line-ink` | `rgb(244 244 242 / 0.16)` | Hairline rule **on ink** (inverted sections). |

### Accent — the one surviving color

| Token | Value | Role |
|---|---|---|
| `--pal-accent` | `#4b2bf5` | The single accent. Ticks, links, active word, section numbers, hover state, LEDs. Used **sparingly**. |
| `--pal-accent-2` | `#6a44fb` | Lighter accent, used on ink panels (auth kicker, list markers). |

### Inherited semantic

| Token | Value | Role |
|---|---|---|
| `--positive` | `#0f7a53` | "Live"/OK status dots, success alerts, ticker OK dot. (From `onewooz.css`.) |

### Usage rules
- **Text on ink** uses opacity ramps of paper, not separate tokens:
  `rgb(244 244 242 / 0.72)` (lead), `/ 0.6` (muted/mono), `/ 0.82` (auth list).
- Selection: `background: ink; color: paper` (inverted).
- Focus ring: `2px solid var(--pal-accent)`, `outline-offset: 2px`.
- Buttons hover → accent fill. Links hover → ink (from accent).
- Imagery: `filter: grayscale(1) contrast(1.04)` by default; storefront cells
  restore `grayscale(0) contrast(1)` on hover.

---

## 3. Typography

Three families. Loaded from Google Fonts (already in the CSP allowlist):
- **Inter** — loaded in `templates/base.html` (`wght 400;500;600;700`).
- **Space Grotesk** + **JetBrains Mono** — loaded in `_shell.html`
  (`Space Grotesk 400;500;600;700`, `JetBrains Mono 400;500;600`).

| Token | Stack | Role |
|---|---|---|
| `--pal-font-display` | `"Space Grotesk", "Inter", system-ui, sans-serif` | Headings, display, brand, stat/price values. |
| `--pal-font-mono` | `"JetBrains Mono", ui-monospace, "Cascadia Mono", monospace` | **Metadata, labels, buttons, nav links** — the signature. |
| `--pal-font-body` | `"Inter", system-ui, -apple-system, sans-serif` | Body copy, leads. |

**Base body:** `font-size: 1rem; line-height: 1.55;` antialiased,
`text-rendering: optimizeLegibility`.

### Display / heading scale (fluid `clamp()`)

| Class | `font-size` | Letter-spacing | Line-height |
|---|---|---|---|
| `.pal-display` | `clamp(2.9rem, 1.1rem + 7vw, 6.25rem)` | `-0.04em` | `0.95` |
| `.pal-h2` | `clamp(2rem, 1.2rem + 3vw, 3.5rem)` | `-0.032em` | (from base `1.03`) |
| `.pal-h3` | `clamp(1.2rem, 1rem + 0.7vw, 1.55rem)` | `-0.02em` | `1.15` |
| headings base (`h1–h4`, `.pal-display`) | — | `-0.025em` | `1.03`, weight **500**, `text-wrap: balance` |
| `.pal-pagehead__title` | `clamp(2.5rem, 1.5rem + 4vw, 4.5rem)` | `-0.035em` | `0.98` |

Headings are weight **500** (not bold) — grotesk at medium weight is the look.

### Body / supporting type

| Class | Size | Notes |
|---|---|---|
| `.pal-lead` | `clamp(1.05rem, 1rem + 0.45vw, 1.35rem)` | `line-height 1.5`, color `ink-2`, `max-width 48ch`, `text-wrap: pretty` |
| `.pal-muted` | inherits | color `muted` |
| `.pal-measure` | — | `max-width: 42ch` |
| `.pal-prose` | `1.02rem` | `line-height 1.7`, color `ink-2`, `max-width 46rem` |

### Monospace metadata — the signature

`.pal-mono`: `font-size 0.75rem`, weight 500, `letter-spacing 0.14em`,
`text-transform: uppercase`, color `muted`, `font-feature-settings: "tnum"`.
Variant `.pal-mono--accent` → accent color.

Mono is used at many small sizes across components — typical range
`0.55rem – 0.75rem`, letter-spacing `0.06em – 0.16em`, always uppercase.

### Accent typographic devices
- **`.pal-marker`** — section marker "`02 — CAPABILITIES`": mono `0.72rem`,
  `letter-spacing 0.16em`, flex row with `gap 0.75rem`; `::after` draws a
  `1px` rule filling remaining space; `.pal-marker__num` is accent-colored.
- **`.pal-accent-word`** — a single word with a precise `2px` accent underline
  (`::after`, `bottom: -0.06em`), `white-space: nowrap`. Not a highlight.
- **`.pal a.pal-link`** — accent color, no underline except a
  `1px solid currentColor` bottom border; hover → ink.

---

## 4. Layout, spacing & rhythm

### Containers
- `--pal-container: 80rem` max width; `.pal-container` centers with
  `padding-inline: var(--pal-gutter)`.
- `--pal-gutter: clamp(1.25rem, 0.8rem + 2vw, 3rem)`.
- `.pal-container--narrow`: `max-width: 52rem`.

### Sections
- `.pal-section`: `padding-block: clamp(4rem, 2rem + 7vw, 8rem)` — the section rhythm.
- `.pal-section--tight`: `clamp(2.5rem, 1.5rem + 3vw, 4rem)`.
- `.pal-section--ink`: inverts to `background: ink; color: paper` (and remaps
  lead/mono/marker/grid/matrix colors to the ink ramp).
- `.pal-ruled`: `border-top: 1px solid var(--pal-line)` (ink variant uses
  `--pal-line-ink`).

### Blueprint grid (the engineered backdrop)
`.pal-blueprint::before` paints faint vertical lines every `100%/6` (a
6-column gutter grid) using `--pal-line-soft`, masked to fade at top/bottom
(`8%`/`92%`). Direct children get `position: relative` to sit above it.

### Bordered grids (hairline-separated cells)
`.pal-grid` is a `1px`-gap grid over a `--pal-line` background with a
`--pal-line` border, so children (`--pal-paper`) appear separated by single
hairlines. Modifiers `.pal-grid--2`, `.pal-grid--3`.
- `< 56rem`: `--3` collapses to 2 columns.
- `< 40rem`: `--2`/`--3` collapse to 1 column.
- On ink: grid background/border become `--pal-line-ink`, cells `--pal-ink`.

### Spacing helpers (pal-scoped)
`.pal-mt-4` `1rem` · `.pal-mt-6` `1.5rem` · `.pal-mt-8` `2rem` ·
`.pal-mt-10` `2.5rem` · `.pal-mt-12` `3rem` · `.pal-center` (`text-align:center`).

---

## 5. Geometry: radii, borders, shadows

- **Radius: `0` everywhere.** Buttons, inputs, cards, panels, chips, alerts,
  selects — all squared. (The base `onewooz.css` defines `--radius 0.875rem`,
  `--radius-sm 0.5rem`, `--radius-full 999px`, but the marketing theme
  overrides every one of them to `0`. Round corners appear only on genuinely
  circular tokens: the pipeline mobile step numbers and status dots.)
- **Borders:** `1px` hairlines using the line tokens are the primary
  structural device. Emphasis borders use `1px solid var(--pal-ink)`.
  Left-accent bars are `2px solid var(--pal-accent)` (alerts).
- **Corner brackets** (`.pal-frame` + `.pal-corner--tl/tr/bl/br`): `14px`
  L-shaped `1.5px solid var(--pal-accent)` brackets pinned to a frame's
  corners (`z-index: 3`). The engineered "registration mark" motif.
- **Shadows:** used only as a **hard offset block**, never a soft glow:
  - Store mock: `box-shadow: 18px 18px 0 -1px rgb(10 10 11 / 0.06)`.
  - Dashboard mock: `box-shadow: 16px 16px 0 -1px rgb(10 10 11 / 0.06)`.
  Text shadow appears once, on the store hero title over imagery
  (`0 1px 12px rgb(0 0 0 / 0.4)`).

---

## 6. Components

### 6.1 Buttons — `.pal-btn`
Mono `0.72rem`, weight 500, `letter-spacing 0.12em`, uppercase.
`padding 0.8125rem 1.375rem`, radius 0, `1px` border.
- **Default (solid):** ink background, paper text, ink border. Hover → accent
  fill (`#fff` text).
- **`.pal-btn--ghost`:** transparent, ink text, `--pal-line` border. Hover →
  accent text + accent border (stays transparent).
- **On ink sections:** solid inverts to paper bg / ink text; hover still → accent.
- **`.pal-btn__arrow`:** translates `3px` right on hover.
- **`.pal-btn-row`:** flex, `wrap`, `gap 0.75rem`.
Transitions: `background / color / border-color 160ms ease`.

### 6.2 Navigation — `.pal-nav`
Sticky (`top:0`, `z-index 50`), translucent paper
(`rgb(244 244 242 / 0.86)`) with `backdrop-filter: blur(10px)` and a bottom
hairline. Inner is `height: 4rem`, space-between flex.
- `.pal-brand` — display font, weight 600, `gap 0.625rem`;
  `.pal-brand__mark` is a `1.5rem` ink square with a mono glyph;
  `.pal-brand__img` is a `1.2rem`-tall logo image.
- `.pal-nav__links` — mono `0.72rem`, `letter-spacing 0.1em`, uppercase,
  `gap 1.75rem`; muted, hover / `[aria-current="page"]` → ink.
- **Mobile menu** `.pal-menu` (JS-free `<details>`, CSP-safe): hidden by
  default; below `62rem` the inline links + ghost button hide and a "Menu"
  disclosure appears. Open state inverts the summary to ink; panel drops
  below the `4rem` bar with mono links separated by soft rules.

### 6.3 Hero — `.pal-hero`
Asymmetric two-column grid `1.05fr 0.95fr`, `gap clamp(2rem,…,5rem)`,
vertically centered; collapses to one column below `64rem`.
Copy rhythm: display `margin-top 1.5rem`, lead `1.75rem`, actions `2.25rem`.
- **`.pal-specs`** — a ruled datasheet under hero copy: 3-column grid with a
  top rule and inter-column right rules; each item has a display-font
  `.pal-specs__value` (`1.5rem`) over a mono `.pal-specs__label` (`0.64rem`).

### 6.4 Rendered storefront mock — `.pal-store`
The product, shown as a framed browser. `1px solid ink`, white panel, the
`18px 18px` hard offset shadow.
- `.pal-viewport__caption` — mono caption row with an accent `.dot`.
- `.pal-store__bar` — top chrome: display-font logo + mono nav
  (`0.58rem`, hidden `< 30rem`).
- `.pal-store__hero` — image `height clamp(9rem,22vw,13rem)`, grayscale;
  overlaid kicker + title (white, text-shadow).
- `.pal-store__grid` — 3-col product grid, hairline-separated cells; each cell
  image is `aspect-ratio 4/5`, grayscale, **restores color on hover**; name
  `0.72rem/500`, mono price `0.64rem`.
- `.pal-tag` — floating mono chip (paper bg, ink border) with an accent `.led`
  square; positioned `--tr` / `--bl` bleeding outside the frame; hidden `< 40rem`.

### 6.5 Logo row — `.pal-logos`
Centered wrap, `gap clamp(1.5rem,…,3.5rem)`; items are display-font
`1rem/600`, `letter-spacing 0.02em`, color `faint`.

### 6.6 Capability matrix — `.pal-matrix`
A ruled table of capabilities. Bordered container (bottom border removed;
rows supply it). Each `.pal-matrix__row` is `[4.5rem | 1fr]`; hover fills
`--pal-paper-2`.
- `.pal-matrix__idx` — mono accent index, right-ruled.
- `.pal-matrix__body` — `[15rem | 1fr]`, `gap 1rem 2rem`, baseline-aligned:
  `.pal-matrix__title` (display `1.1rem`) + `.pal-matrix__desc` (muted `0.95rem`).
- `< 48rem`: index column shrinks to `3rem`; body stacks to one column.
- On ink: borders become `--pal-line-ink`; hover `rgb(244 244 242 / 0.05)`.

### 6.7 Feature / spec cell — `.pal-cell`
Generic bordered grid item, `padding clamp(1.5rem,…,2.25rem)`:
`.pal-cell__idx` (mono accent) → `.pal-cell__title` (display `1.2rem`) →
`.pal-cell__desc` (muted `0.95rem`). Used inside `.pal-grid`.

### 6.8 Stats band — `.pal-stats`
4-column grid divided by `--pal-line-ink` rules (lives on ink). Collapses
`4 → 2 (< 56rem) → 1 (< 30rem)`. `.pal-stat__value` display font
`clamp(2.5rem,…,3.75rem)/500`, `letter-spacing -0.03em`; `.pal-stat__label`
mono `0.64rem` on the ink-muted ramp. Values can animate via `data-count`.

### 6.9 Quote — `.pal-quote`
`max-width 60rem`. `.pal-quote__text` display font, weight **400**,
`clamp(1.5rem,…,2.5rem)`, `line-height 1.2`, `text-wrap: pretty`.
`.pal-quote__by` mono attribution row; `strong` → ink (paper on ink sections).

### 6.10 Page header (interior pages) — `.pal-pagehead`
`padding-block clamp(3.5rem,…,6rem) clamp(2rem,…,3.5rem)`.
`.pal-breadcrumb` mono `0.68rem` (links muted → ink on hover; current in accent).
`.pal-pagehead__title` `clamp(2.5rem,…,4.5rem)`, `letter-spacing -0.035em`.
`.pal-pagehead__lead` `margin-top 1.5rem`.

### 6.11 Pricing — `.pal-plans` / `.pal-plan`
3-column, `1px`-gap, line-bordered grid (collapses to 1 col `< 60rem`).
Each `.pal-plan` is paper, flex column, `padding clamp(1.75rem,…,2.5rem)`,
`position: relative` (for doodles).
- `.pal-plan--featured` inverts to ink.
- `.pal-plan__name` mono `0.72rem`; `.pal-plan__price` display
  `clamp(2.25rem,…,3rem)/500` with a mono `span` for the period suffix;
  `.pal-plan__desc` muted `0.9rem`.
- `.pal-plan__list` — grid `gap 0.75rem`; each `li::before` is an accent mono
  `"→"`.
- `.pal-plan__cta` pinned to bottom (`margin-top: auto`, `padding-top 1.75rem`).

### 6.12 Prose (legal / long-form) — `.pal-prose`
`max-width 46rem`, `1.02rem/1.7`, color `ink-2`. Vertical rhythm via
`> * + * { margin-top: 1.25rem }`. `h2`/`.h3` display font with generous
top margins; links get accent + `currentColor` underline; `strong` → ink;
`ul` is a `gap 0.75rem` grid with no left padding.

### 6.13 Forms & inputs
General marketing form (`.pal-field` / `.pal-input` / `.pal-textarea`) and
the auth/legacy control set (`.input`, `.label`, `.field`, …) share one look:
- Label: mono `~0.64–0.66rem`, `letter-spacing 0.1em`, uppercase, muted.
- Input/textarea/select: full width, `padding ~0.75–0.8rem 0.9rem`,
  `1px solid var(--pal-line)`, radius 0, panel/white background, `font: inherit`.
- **Focus:** `border-color: accent` + `box-shadow: inset 0 0 0 1px accent`
  (a doubled inner rule, no glow), `outline: none`.
- `.pal-textarea` `min-height 8rem`, `resize: vertical`.
- `.pal-select` — native arrow replaced by two `linear-gradient` triangles
  drawn from `--pal-muted` at the right edge.
- `.pal-form-grid` — 2-col (1-col `< 40rem`); `.pal-field--full` spans both.
- `.input--code` — centered mono `1.4rem`, `letter-spacing 0.5em` (OTP).

### 6.14 Chips — `.pal-chipset` / `.pal-chip`
Radio chips: visually-hidden input + mono uppercase `label`
(`0.64rem`, `1px` line border). Hover → accent text/border; `:checked` →
ink fill / paper text; focus-visible → accent outline.

### 6.15 Alerts — `.pal-alert`
`1px` line box with a `2px` **left accent bar**, panel bg, `ink-2` text.
`.pal-alert--ok` swaps the bar to `--positive`.
`.pal-alert__title` display `1.05rem`; `.pal-alert__ref` mono accent.
(Legacy `.alert--info` under `.pal` mirrors this: left `2px` accent bar.)

### 6.16 Info / meta / infocard
- `.pal-infocard` — `1px` line panel, `padding clamp(1.5rem,…,2rem)`;
  display-font `h3` + muted `p`.
- `.pal-meta` — key/value panel, rows hairline-separated; `__k` mono muted,
  `__v` mono ink.

### 6.17 FAQ accordion — `.pal-faq`
JS-free `<details>`. Top + per-item bottom hairlines. `summary` is a
space-between flex row, display font `clamp(1rem,…,1.15rem)/500`,
native marker removed. `.pal-faq__sign` is a `+` built from two accent bars;
the vertical bar collapses (`scaleY(0)`) when `[open]` → becomes `−`.
`.pal-faq__a` muted answer, `max-width 68ch`.

### 6.18 Timeline — `.pal-time`
Left-ruled list; each `.pal-time__item` has an `8px` accent square node on the
rule. `__k` mono muted, `__t` display `1.15rem`, `__d` muted `max-width 58ch`.

### 6.19 Footer — `.pal-footer`
Top hairline, `padding-block 3.5rem 2.5rem`. `__top` is a wrap flex
(brand+note left, link columns right). `__col h4` mono `0.66rem` faint;
column links muted → ink on hover. `__base` is a top-ruled mono row
(`0.64rem`, uppercase, faint) — copyright + tagline.

---

## 7. Illustrative / creative layer

Technical SVG illustration in the same engineered register — "schematic
markup", never cartoon. All loops stop under `prefers-reduced-motion`.

- **`.pal-draw`** — SVG line art that **draws itself** on reveal: JS measures
  each stroke's length and sets its dash; CSS transitions
  `stroke-dashoffset 1300ms` with the expo-out ease.
- **`.pal-ticker`** — a terminal order feed: an infinite marquee
  (`pal-ticker 48s linear`, `translateX(-50%)`), paused on hover; items are
  mono, hairline-separated, with a `6px` accent (or `--positive`) square dot.
- **`.pal-module`** — management-module cells: a `2.5rem` line-icon box
  (`stroke: currentColor; stroke-width 1.6`) that turns accent on hover;
  display-font name + muted desc + a mono corner `__tag`.
- **`.pal-pipeline`** — commerce pipeline SVG (click → doorstep): grey `track`,
  animated accent `flow` (dashed, `pal-flow` marching ants), square `node`s,
  mono `label`/`sub`, an accent `packet` in transit. Below `40rem` the SVG
  hides and **`.pal-pipe-m`** shows a vertical numbered list (circular
  `1.9rem` step numbers on a left rule) with a descending accent `__flow` dot.
- **`.pal-dash`** — operations-dashboard mock: `1px ink` frame + `16px 16px`
  hard shadow; mono top bar with a pulsing `--positive` "live" dot; 3-col KPI
  grid (display values, `tabular-nums`); a bar chart whose bars
  `scaleY(0→1)` on reveal, staggered `60ms` each via `--b`, last bar accent.
- **`.pal-doodle` / `.pal-note`** — technical annotation "doodles": accent line
  SVG + mono note, absolutely positioned, `pointer-events: none`.
- **`.pal-map`** — logistics map: line `land`, animated dashed accent `route`,
  accent `hub` with a pinging `hub-ring` (`pal-ping 2.6s`, `scale→3.4`),
  mono place labels, an accent `origin` + faint `origin-ring`.

---

## 8. Motion — the entrance system

Palantir tempo: **slow, unhurried, settles once, never loops.**

### Tokens (on `.pal`)
| Token | Value | Meaning |
|---|---|---|
| `--rv-dur` | `1050ms` | Reveal duration. |
| `--rv-ease` | `cubic-bezier(0.16, 1, 0.3, 1)` | Expo-out — heavy settle. |
| `--rv-shift` | `26px` | Rise distance. |
| `--rv-blur` | `12px` | Focus-in blur — the signature. |

### How it works
- `_shell.html` adds the class **`pal-anim`** to `<html>` *before first paint*,
  **only if motion is allowed**. Initial hidden states are gated on `.pal-anim`,
  so there is **no flash of hidden content**, and reduced-motion / no-JS users
  get a fully rendered, still page.
- The generic reveal is **fade + rise + focus-in**: hidden items start
  `opacity 0`, `translateY(26px)`, `blur(12px)`; `.is-visible` settles all three.
  Applied to markers, leads, button rows, quotes, infocards, fields, plan CTAs,
  viewports, and every heading level.
- **Headings** get a **line-by-line reveal**: JS splits on `<br>` into
  `.pal-line` masks whose `.pal-line__inner` slides up `translateY(118%)` from
  below, in focus, one line after the next (staggered `90ms`). Unsplit
  fallback is a `clip-path` mask-wipe.
- **Section markers**: the flanking rule draws out (`scaleX(0→1)`) `220ms`
  after the label lands.
- **Framed media** (`.pal-viewport`): settles up from a slight `scale(0.97)`.
- **Groups** (`.pal-matrix`, `.pal-grid`, `.pal-plans`, `.pal-stats`,
  `.pal-specs`, `.pal-logos`, `.pal-prose`): children rise + focus in sequence
  when the group enters view (per-child delay `min(i,8)*80ms`, set via the DOM
  — never inline styles, to stay CSP-safe). An animated item *nested inside* a
  group rides along with its group child rather than hiding on its own.
- **Per-section cascade:** top-level items in a section arrive in sequence
  (`min(i,6)*100ms`).
- **Counters** (`[data-count]`): start at 0, count up over `1300ms`
  (cubic ease-out), `toLocaleString("en-IN")`, with optional
  `data-prefix`/`data-suffix`. Markup holds the final value for the
  no-JS / reduced-motion case.
- **Reveal trigger:** `IntersectionObserver`
  (`rootMargin: "0px 0px -8% 0px"`, `threshold 0.08`); each target settles once
  then is unobserved. No-IO fallback reveals everything immediately.

### Reduced motion
Under `prefers-reduced-motion: reduce`, all looping animations
(ticker, pipeline flow, packet, map route/hub-ring, auth dot, pipe-m flow) and
the storefront hover, dashboard bars and draw-in transitions are disabled;
the page renders fully and still.

---

## 9. Authentication surface — `.pal-auth`

A split "commerce OS" console. Only `_auth_shell.html` changes structurally;
the legacy `onewooz.css` form classes are restyled under `.pal` (see §6.13),
so sign-in, sign-up, MFA, magic link and TOTP all transform without edits.

- Layout: `min-height 100dvh`, grid `1.05fr 0.95fr`; below `60rem` the left
  panel hides and it becomes one column.
- **Left `.pal-auth__panel`** (ink): a blueprint grid (`3rem` squares of
  `rgb(244 244 242 / 0.06)` lines) radially masked toward the top-left; inner
  is a space-between flex column. `.pal-auth__kicker` accent-2; headline
  display `clamp(1.9rem,…,3rem)`, `max-width 15ch`; `.pal-auth__stack` is a
  hairline-ruled feature list (mono accent-2 index spans); `.pal-auth__status`
  is a mono system-status line with a pulsing `--positive` `.pal-auth__dot`
  (`pal-pulse 2.4s`).
- **Right `.pal-auth__main`** (paper): centered form column, `max-width 26rem`.
  `.auth__title` display `clamp(1.6rem,…,2.1rem)`; `.auth__subtitle` muted;
  `.divider` a mono ruled "or"; `.code-block` mono on `--pal-paper-2`;
  `.btn--link` a bare mono accent link.

---

## 10. Quick reference — full class inventory

**Type/meta:** `pal-display` `pal-h2` `pal-h3` `pal-lead` `pal-muted`
`pal-measure` `pal-mono` `pal-mono--accent` `pal-marker` `pal-marker__num`
`pal-accent-word` `pal-link`
**Layout:** `pal-container` `pal-container--narrow` `pal-section`
`pal-section--tight` `pal-section--ink` `pal-ruled` `pal-blueprint`
`pal-grid` `pal-grid--2` `pal-grid--3` `pal-mt-4/6/8/10/12` `pal-center`
**Buttons/frame:** `pal-btn` `pal-btn--ghost` `pal-btn__arrow` `pal-btn-row`
`pal-frame` `pal-corner--tl/tr/bl/br`
**Chrome:** `pal-nav` `pal-nav__inner` `pal-brand` `pal-brand__mark`
`pal-brand__img` `pal-nav__links` `pal-nav__actions` `pal-menu`
`pal-menu__panel` `pal-footer` (`__top __note __cols __col __base`)
**Hero/store:** `pal-hero` (`__grid __display __lead __actions`) `pal-specs`
(`__item __value __label`) `pal-viewport` (`__caption`) `pal-store`
(`__bar __logo __nav __hero __hero-copy __hero-kicker __hero-title __grid __cell __cell-name __cell-price`)
`pal-tag` (`--tr --bl`, `.led`) `pal-logos` (`__item`)
**Content blocks:** `pal-matrix` (`__row __idx __body __title __desc`)
`pal-cell` (`__idx __title __desc`) `pal-stats` `pal-stat`
(`__value __label`) `pal-quote` (`__text __by`) `pal-pagehead`
(`__title __lead`) `pal-breadcrumb` `pal-plans` `pal-plan`
(`--featured __name __price __desc __list __cta`) `pal-prose`
**Forms:** `pal-field` (`__label`) `pal-input` `pal-textarea` `pal-select`
`pal-form-grid` `pal-field--full` `pal-chipset` `pal-chip` `pal-alert`
(`--ok __title __ref`) `pal-infocard` `pal-meta` (`__row __k __v`)
`pal-faq` (`__sign __a`) `pal-time` (`__item __k __t __d`) `pal-shield`
**Illustration:** `pal-draw` `pal-ticker` (`__track __item __dot __dot--ok __k`)
`pal-module` (`__ico __name __desc __tag`) `pal-pipeline` (`.track .flow .node .node-i .label .sub .packet`)
`pal-pipe-m` (`__num __label __sub __flow`) `pal-dash`
(`__top __kpis __kpi __kpi-val __kpi-label __chart __chart-head __bars`)
`pal-doodle` (`--plan`) `pal-note` `pal-map` (`.land .route .hub .dot .hub-ring .origin .origin-ring .origin-label`)
**Auth:** `pal-auth` (`__panel __inner __kicker __headline __stack __status __dot __main __wrap __brand`)
**Motion state:** `pal-anim` (on `<html>`) · `is-visible` · `pal-split`
`pal-line` `pal-line__inner`

---

*Generated from `onewooz/static/css/onewooz-pal.css`, `templates/web/_shell.html`,
and `templates/base.html`. When the CSS changes, update this file to match — it
is documentation, not a source the build reads.*
