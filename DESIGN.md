---
name: Food diary
description: A crisp, minimal daily agenda for meals and symptoms.
colors:
  canvas: "#fafbf9"
  surface: "#fff"
  ink: "#232a26"
  muted: "#677069"
  line: "#e2e7e1"
  accent: "#244c3a"
  accent-hover: "#163626"
  wash: "#edf3ed"
  error: "#a1322d"
  focus: "#3e7257"
  field-line: "#b4bfb6"
  soft-hover: "#dce9df"
typography:
  headline:
    fontFamily: '"DM Sans", "Segoe UI", sans-serif'
    fontSize: "36px"
    fontWeight: 600
    lineHeight: 1.15
    letterSpacing: "-.035em"
  title:
    fontFamily: '"DM Sans", "Segoe UI", sans-serif'
    fontSize: "18px"
    fontWeight: 600
    lineHeight: 1.5
    letterSpacing: "-.015em"
  body:
    fontFamily: '"DM Sans", "Segoe UI", sans-serif'
    fontSize: "16px"
    fontWeight: 400
    lineHeight: 1.5
  label:
    fontFamily: '"DM Sans", "Segoe UI", sans-serif'
    fontSize: "14px"
    fontWeight: 600
    lineHeight: 1.5
rounded:
  compact: "4px"
  control: "8px"
  navigation: "10px"
  dialog: "16px"
spacing:
  base: "4px"
  small: "8px"
  compact: "12px"
  medium: "16px"
  row: "20px"
  section: "24px"
  large: "32px"
  generous: "48px"
components:
  button-primary:
    backgroundColor: "{colors.accent}"
    textColor: "{colors.surface}"
    typography: "{typography.label}"
    rounded: "{rounded.control}"
    padding: "10px 18px"
  button-primary-hover:
    backgroundColor: "{colors.accent-hover}"
  button-secondary:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.ink}"
    typography: "{typography.label}"
    rounded: "{rounded.control}"
    padding: "10px 18px"
  button-secondary-hover:
    backgroundColor: "{colors.wash}"
  button-danger:
    backgroundColor: "transparent"
    textColor: "{colors.error}"
    typography: "{typography.label}"
    rounded: "{rounded.control}"
    padding: "10px 18px"
  input:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.ink}"
    typography: "{typography.body}"
    rounded: "{rounded.control}"
    padding: "12px"
  date-navigation:
    backgroundColor: "{colors.surface}"
    rounded: "{rounded.navigation}"
    padding: "4px"
  meal-row:
    padding: "20px 0"
  symptom-options:
    rounded: "{rounded.control}"
  meal-dialog:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.ink}"
    rounded: "{rounded.dialog}"
    width: "calc(100% - 32px)"
---

# Design System: Food diary

## Overview

**Creative North Star: "The daily agenda"**

The user-selected direction is crisp and minimal. An off-white canvas, charcoal text, thin dividers and deep green actions give the diary a quiet, practical character. Typography and spacing make saved entries easy to scan.

The system is compact enough for a phone without compressing touch targets. Surfaces stay flat until an editor or status message needs to sit above the page. The implemented source is `app/static/app.css` and the templates in `app/templates/`; `.impeccable/design.json` adds component previews and tokens outside the frontmatter schema.

**Key Characteristics:**

- One local sans-serif family with restrained weight and size changes.
- Flat, divided content with green reserved for actions and selected states.
- Readable phone forms, visible focus and short, optional motion.

## Colors

The palette pairs green accents with lightly green-tinted neutrals. Frontmatter preserves the exact implemented values.

### Primary

Deep green `accent` carries primary actions, selected controls and import links; `accent-hover` deepens primary hover. Pale `wash` supports selection and subtle hover, while `soft-hover` strengthens compact import and meal-marker hover. `focus` supplies the keyboard outline.

### Neutral

`canvas` is the page; `surface` is the white form, navigation and overlay surface. `ink` carries content and `muted` carries optional information and placeholders. `line` divides sections and secondary controls; the stronger `field-line` defines editable fields. The functional `error` color is reserved for errors and destructive text actions.

## Typography

Self-hosted DM Sans is used throughout, with Segoe UI and sans-serif fallbacks. The headline, section title, body and label roles are defined above. There is no separate display face or fixed scale ratio.

Date headlines reduce to 32px on phones; settings headlines reduce to 30px. Dialog titles are 23px and settings section titles are 20px. Meal titles use 17px/600 with 1.4 line height, reducing to 16px on phones; meal descriptions move from 15px/1.55 to 16px. Metadata uses 12–14px. Form text remains 16px; textareas use 1.6 line height. Dates and times use tabular numerals.

## Layout

The desktop header and diary share a 1120px maximum width and 48px horizontal padding. The diary uses a 256px date column, a flexible content column and a 72px gap. The sidebar sticks 32px from the top. At 1400px and above, the container expands to 1160px and the gap to 96px.

At 760px and below, the diary becomes a single column, capped at 560px with 24px side padding. Date navigation scrolls with the page to preserve vertical space. At 360px and below, the header, diary and editor use 16px side padding. Spacing generally follows the 4px base; content rows use generous vertical separation and thin rules. Settings use a 720px container; authentication uses 440px. Export dates share two flexible columns, then stack at 420px and below.

## Elevation & Depth

Content and controls are flat at rest. Only overlays are shadowed: the dialog uses `0 20px 80px #14291a30` with a `#1c292757` backdrop, and status toasts use `0 6px 24px #1a352324`. The sidecar carries these exact extension tokens.

## Shapes

Controls have gently curved corners using the control radius. Date navigation is slightly softer. The centered editor uses the dialog radius; on phones its top corners become 20px and bottom corners are square. Meal markers are circles, sized 36px on desktop and 32px on phones. Borders are 1px. Line icons use a 1.7px rounded stroke and are usually 20px.

## Components

- **Buttons:** primary green, white bordered secondary, and transparent red destructive variants share 14px semibold labels and a 46px minimum height. Icon actions are 44px square. Hover changes color over `.16s ease`; pressing darkens brightness to `.95`. Disabled buttons use `.5` opacity and a waiting cursor.
- **Fields:** white inputs and textareas use the stronger field border, control radius and 12px padding. Inputs have a 48px minimum height. Focus changes the border to green; keyboard focus adds a 3px outline with 3px offset. Errors appear as nearby red text.
- **Date navigation:** a white bordered strip contains previous/next icon links and a native date input. A separate green text link returns to today when applicable.
- **Meal rows:** full-width text buttons sit between thin dividers, with a circular add/check marker, food description, optional symptoms and time, and a chevron. Hover changes the title and marker tint. Optional import suggestions align with the food text underneath and truncate only their preview.
- **Symptom options:** three equal radio segments share one outlined container. Selection uses the pale green wash, green text and heavier weight; keyboard focus is drawn inside the segment. The labels distinguish missing data, no symptoms and recorded symptoms.
- **Meal editor:** a native dialog is centered and capped at 520px on desktop, then becomes a full-width bottom sheet on phones. The body scrolls while the heading and action row remain visible. Height is capped at 90dvh on desktop and 94dvh on phones; phone actions include the bottom safe-area inset. Entry moves upward 16px while opacity changes from `.8` to `1` over `.2s cubic-bezier(.2,.8,.2,1)`. Reduced motion disables animations and transitions.
- **Status:** a compact green toast sits above the bottom safe area. Inline status and error text remain close to the action they describe.

## Do's and Don'ts

### Do:

- **Do** reuse the existing color roles, local font and control shapes.
- **Do** preserve 44px or larger action targets and visible keyboard focus.
- **Do** let phone date navigation scroll and keep editor actions visible.
- **Do** allow saved food and symptom text to wrap without clipping.

### Don't:

- **Don't** turn the divided diary into a grid of elevated cards.
- **Don't** add decorative imagery, gradients or extra accent palettes to this minimal system.
- **Don't** use color alone to communicate selection or recording status.
- **Don't** retain animation when reduced motion is requested.
