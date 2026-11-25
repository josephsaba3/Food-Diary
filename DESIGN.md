---
name: Food diary
description: A calm, phone-first diary of meals, symptoms and watch data, built for sharing with a dietitian.
colors:
  ink: "#15201B"
  muted: "#5B6660"
  faint: "#9AA39E"
  bg: "#FFFFFF"
  soft: "#F3F6F4"
  softer: "#F7F9F8"
  paper: "#FBFCFB"
  line: "#E3E7E5"
  line-soft: "#EEF1EF"
  line-strong: "#D5DBD8"
  green: "#1E6B4F"
  green-dark: "#154E3A"
  green-soft: "#E8F1ED"
  amber: "#B45309"
  amber-ink: "#8A4A0B"
  amber-deep: "#7A3F06"
  amber-text: "#5E3206"
  amber-soft: "#FDF5EC"
  amber-tint: "#FDF0E1"
  amber-line: "#EBC9A3"
  amber-dash: "#D9B48A"
  blue: "#2D5B9A"
  blue-deep: "#1D3F6E"
  blue-soft: "#EEF3FA"
  chart-rest: "#9DB4D6"
  chart-up: "#D98A3D"
  error: "#A1322D"
typography:
  page-title:
    fontFamily: '"Inter", system-ui, sans-serif'
    fontSize: "30px"
    fontWeight: 700
    letterSpacing: "-.02em"
  sheet-title:
    fontFamily: '"Inter", system-ui, sans-serif'
    fontSize: "22px"
    fontWeight: 700
  section-title:
    fontFamily: '"Inter", system-ui, sans-serif'
    fontSize: "17px"
    fontWeight: 650
  body:
    fontFamily: '"Inter", system-ui, sans-serif'
    fontSize: "15px"
    fontWeight: 400
    lineHeight: 1.45
  label-caps:
    fontFamily: '"Inter", system-ui, sans-serif'
    fontSize: "12px"
    fontWeight: 600
    letterSpacing: ".04em"
    textTransform: uppercase
rounded:
  chip: "999px"
  small: "10px"
  control: "12px"
  tile: "14px"
  card: "16px"
  sheet: "24px"
spacing:
  gutter: "20px"
  gutter-narrow: "16px"
  tile-gap: "8px"
  section: "26px"
---

# Design System: Food diary

## Overview

**North star: "a calm notebook that's honest about gaps."** White pages, near-black green text, thin grey rules and one green for actions. Amber always means symptoms (including symptoms not yet recorded), blue always means the watch. The source of truth is `app/static/app.css` and the templates in `app/templates/`; the five screens follow the "Food Diary redesign" boards (Today, Log sheet, Health, Week, Report).

- One self-hosted family, Inter (Latin and Latin Extended subsets, OFL), weights 400–700.
- Flat surfaces: tinted tiles and 1px borders, shadows only on the floating log button, segmented selections, menus and sheets.
- Phone first at 390px with a bottom tab bar; from 900px a 248px side navigation replaces it and sheets become centred dialogs.

## Colors

- **Green** (`green`, hover `green-dark`, tint `green-soft`): primary buttons, the log button, selected navigation, logged cells in the week grid, the "Now" line, steps bars and Body Battery.
- **Amber** (`amber` family): anything about symptoms. Reported symptoms use `amber-soft` panels with `amber-text`; "How did you feel after?" uses a dashed `amber-dash` pill; severity and stool selections fill with `amber`. "Not recorded" is amber text, never green, so a blank never reads as "fine".
- **Blue** (`blue` family): watch data on Today (the heart-rate note, the steps tile) and the heart-rate line. Stress bars use `chart-rest` at 0–25 and `chart-up` above.
- **Neutrals:** `ink` text, `muted` secondary text, `faint` for future days and disabled arrows, `soft`/`softer` tile and empty-slot fills, `line` borders, `line-soft` table rules, `line-strong` switches and empty cells.

## Typography

Page titles are 30px/700 (34px on desktop) under a 13px/500 muted eyebrow with the date. Section titles are 17px/650; sheet titles 22px/700. Field labels and card headings use 12–13px uppercase caps with .04em tracking. Body text is 15px, inputs 16px (no zoom on iOS). Numbers in tiles, times and tables use tabular numerals.

## Layout

- **Phone:** 20px gutter (16px under 380px), content up to 560px, bottom tab bar (Today, Week, +, Health, Report) with a 56px green log button raised into it. Pages leave 96px plus the safe area at the bottom.
- **Desktop (≥900px):** fixed 248px side navigation with the brand, a full-width "Log food or symptoms" button, the four sections and Settings at the bottom. Pages are 760px wide; Health and Report are 1040px. Report puts its options beside a sticky preview; Health puts the daily log beside the day-by-day charts.
- **Sheets:** the Log sheet, notes and Health's day popup are native dialogs that rise from the bottom on phones (24px top corners, grip handle, scrolling body, sticky footer above the safe area) and centre at up to 560px on desktop.

## Components

- **Tiles:** tinted (`soft`, `amber-soft`, `blue-soft`) at 14px radius on Today and Week; outlined 16px tiles with 26px values on Health. Steps tiles can show a goal meter.
- **Week strip:** seven 64px day cells with a dot (green logged, amber with symptoms); the selected day is solid `ink`; future days are `faint`; arrows step a week.
- **Timeline:** a 48px time rail with a 1.5px line, meal cards (16px radius, caps heading, menu button, food chips), empty slots as `softer` rows with a plus, and a green "Now" line on today.
- **Chips and pills:** food chips are `soft` rounded labels (removable ones `green-soft`); choice pills are outlined and fill `ink` when selected; symptom pills fill `amber-tint` with an `amber` border.
- **Segmented control:** `soft` track, white raised selection, used for "How are you feeling?" and Health's Day/Week/Month/90 days.
- **Severity and stool boxes:** 44px squares in rows of 6 and 7; the chosen one fills `amber`. These and the symptom pills only write free text ("Bloating, cramps · severity 2/5 (mild) · Bristol type 5 · notes").
- **Day chart:** stacked lanes (heart rate with a resting reference and peak label, stress bars, steps bars, Body Battery line) over waking hours, with dashed meal lines, dark meal tags that pick the "Around lunch" card, and a cursor readout.
- **Week grid:** 22px cells per meal slot (green logged, amber with symptoms, outlined empty) and a note icon.
- **Report:** toggles are 46×28 switches; the preview is a `paper` document that prints on its own (navigation, options and buttons are hidden by the print stylesheet).

## Do's and Don'ts

- **Do** keep amber for symptoms and blue for the watch; never use colour alone (every state also has text or a label).
- **Do** keep 44px touch targets, visible focus rings (2px green) and 16px input text.
- **Do** say "not recorded" for blanks, and show watch readings as measured, never as a cause.
- **Don't** add a second accent colour, gradients or decorative imagery.
- **Don't** nest cards in cards or add shadows to resting content.
- **Don't** keep motion when reduced motion is requested (sheets only slide 24px over .22s).
