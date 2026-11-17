---
name: Hydroponic Leaf Research
description: Restrained white-and-green interfaces for human-reviewed projected leaf-area measurement.
colors:
  ui-ink: "#182b25"
  ui-muted: "#50645b"
  ui-green: "#176147"
  ui-green-hover: "#104d38"
  ui-paper: "#fff"
  ui-bg: "#f5f7f5"
  ui-line: "#ccd8d0"
  ui-input: "#74877d"
  ui-danger: "#a52623"
  ui-warn: "#73520c"
  ui-focus: "#154c96"
  ui-soft: "#eaf3ed"
  ui-error-bg: "#fff4f2"
  workspace-focus: "#2779d4"
  workspace-disabled-bg: "#e8ede9"
  workspace-disabled-ink: "#52655a"
  review-badge-bg: "#fff5da"
  review-badge-ink: "#624807"
typography:
  body:
    fontFamily: 'system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif'
    fontSize: "1rem"
    fontWeight: 400
    lineHeight: 1.5
  workspace-headline:
    fontFamily: 'system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif'
    fontSize: "1.5rem"
    fontWeight: 700
    lineHeight: 1.3
    letterSpacing: "-.02em"
  account-headline:
    fontFamily: 'system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif'
    fontSize: "1.75rem"
    fontWeight: 700
    lineHeight: 1.2
    letterSpacing: "-.025em"
  section-title:
    fontFamily: 'system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif'
    fontSize: "1.25rem"
    fontWeight: 700
    lineHeight: 1.35
    letterSpacing: "-.01em"
  label:
    fontFamily: 'system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif'
    fontSize: ".9375rem"
    fontWeight: 650
    lineHeight: 1.5
  helper:
    fontFamily: 'system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif'
    fontSize: ".875rem"
    lineHeight: 1.5
  button:
    fontFamily: 'system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif'
    fontSize: ".9375rem"
    fontWeight: 600
    lineHeight: 1.25
  guide-headline:
    fontFamily: 'system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif'
    fontSize: "clamp(1.8rem,4vw,2.35rem)"
    fontWeight: 700
    lineHeight: 1.2
    letterSpacing: "-.04em"
rounded:
  square: "0px"
  subtle: "4px"
  control: "8px"
  guide: "14px"
spacing:
  ui-space-1: "4px"
  ui-space-2: "8px"
  ui-space-3: "12px"
  ui-space-4: "16px"
  ui-space-5: "24px"
  ui-space-6: "32px"
  ui-space-7: "48px"
components:
  button-primary:
    backgroundColor: "{colors.ui-green}"
    textColor: "{colors.ui-paper}"
    typography: "{typography.button}"
    rounded: "{rounded.control}"
    padding: "10px 14px"
  button-primary-hover:
    backgroundColor: "{colors.ui-green-hover}"
  button-secondary:
    backgroundColor: "{colors.ui-paper}"
    textColor: "{colors.ui-green}"
    typography: "{typography.button}"
    rounded: "{rounded.control}"
    padding: "10px 14px"
  button-quiet:
    backgroundColor: "transparent"
    textColor: "{colors.ui-muted}"
    typography: "{typography.button}"
    rounded: "{rounded.control}"
    padding: "10px 14px"
  field:
    backgroundColor: "{colors.ui-paper}"
    textColor: "{colors.ui-ink}"
    typography: "{typography.body}"
    rounded: "{rounded.control}"
    padding: "10px 12px"
  workspace-section:
    backgroundColor: "{colors.ui-paper}"
    rounded: "{rounded.square}"
    padding: "24px"
  review-badge:
    backgroundColor: "{colors.review-badge-bg}"
    textColor: "{colors.review-badge-ink}"
    rounded: "{rounded.subtle}"
    padding: "5px 8px"
  session-recovery:
    backgroundColor: "{colors.ui-error-bg}"
    rounded: "{rounded.control}"
    padding: "16px"
---

# Design System: Hydroponic Leaf Research

## Overview

**Creative North Star: "The Research Workspace"**

This is the implemented white-and-green scientific workspace: clear system typography, restrained action color, strong text contrast and minimal decoration. Flat sections, direct controls and explicit measurement units make repeated work easy to scan. The design follows the user's pinned direction rather than a seeded visual world.

This document captures the isolated candidate implementation. CSS remains the executable source of truth: the byte-identical shared foundations are `src/auth_preview/static/research-ui.css` and `src/webapp/static/research-ui.css`; the two `style.css` files and `src/webapp/static/user-guide.css` supply surface differences. Frontmatter records reusable observed values, not a newly introduced theme. The JSON sidecar carries extensions and illustrative copies of existing primitives; it does not load styles into the application.

Surface mode is owned by `.impeccable/surfaces/research-workspace.md`: account, institution and measurement surfaces are **Operate**; the help page is **Read**. There is no product-wide mode override here.

**Key Characteristics:**

- White work surfaces on a quiet neutral background.
- Green actions, visible focus and plain status language.
- System fonts, flat sections and bounded explanatory text.
- Measurement imagery and human review ahead of decorative elements.
- One supplied light theme; no external font requests.

## Colors

The frontmatter's `ui-*` names map directly to `--ui-*` custom properties in both shared stylesheets. Other named colors capture actual surface rules rather than new CSS variables.

### Primary

**Research green** (`ui-green`) identifies meaningful actions, links and selected controls. `ui-green-hover` is the workspace primary hover declaration. The guided workspace's more specific primary-action rule holds its green background on hover; do not assume every primary instance changes shade. `ui-soft` supports subtle hover and selection states.

### Neutral

`ui-ink` is the main reading color; `ui-muted` supports secondary text without replacing required labels. `ui-paper` is the work surface, `ui-bg` the page background, `ui-line` the quiet structural separator and `ui-input` the stronger field border. A separator color is not a substitute for the field border.

### Semantic

`ui-danger` and `ui-error-bg` support failures and recovery. `ui-warn` is the shared warning ink token; the actual review badge has its own observed background and ink pair. Guided disabled primary controls use the documented workspace-disabled pair. Account notices and membership pills retain their existing surface-specific colors rather than being recolored by this document.

Blue is functional focus feedback. Shared/account focus uses `ui-focus`; the workspace's later focus declarations use `workspace-focus` for buttons, inputs, selects, summaries and integrated navigation. Textareas and other shared focus targets retain the shared treatment.

**The Truthful State Rule.** Color accompanies a label, state attribute, border or text explanation; it never establishes approval, calibration validity or scientific units by itself.

## Typography

Use the single system sans stack in the frontmatter for interface and reading text. Native platform fonts render locally; there is no display-font pairing or external font dependency. Body copy uses a 16px root and the shared body line-height. The guide declares a 1.65 root line-height, but the loaded shared body rule supplies 1.5 to its body; do not describe the root declaration as the computed body value.

The hierarchy is task-oriented: workspace headlines, section titles, labels and helper text. Workspace headings reduce to 1.25rem below 600px. Account headings reduce to 1.5rem at 440px and below. Account section headings use a 1.3 line-height; the workspace section-title token records its own 1.35 rule. The guide has the fluid headline in frontmatter. Prominent result area uses 1.5rem at weight 650; smaller summary values use 1.25rem, reducing to 1.125rem on phones.

Buttons in the workspace use the button role. Account buttons retain inherited 1rem text, weight 650 and a 1.35 line-height. Labels remain attached to native controls. Tables, metrics and leaf summaries use tabular numerals. Most task explanations are bounded to 65-72ch; help paragraphs are bounded to 68ch.

**The Unit Rule.** Keep px and cm² visible with their respective measurement context. Typography must not visually promote an uncalibrated pixel result as a physical area.

## Layout

The content shell is bounded at 1120px. The spacing primitives record the shared 4/8/12/16/24/32/48px rhythm; actual controls also use observed 10px vertical padding and surface-specific gaps. Do not mechanically replace those values.

The workspace has 24px horizontal desktop gutters, 24px white sections and a single primary task control beside its relevant content. Below 600px, page gutters are 16px and section padding is 18px 16px. The ready-photo block is bounded at 800px on wider layouts; its preview and real Analyze action are grouped together. The review photo comes before editing controls. Tool groups wrap or reflow in normal page flow.

The seven real workflow states remain Plant, Photo, Calibration, Analyze, Review, Approve and Results. At 600px and above their progress controls use seven columns. Below 600px, the progress disclosure uses four columns and labels remain visible when expanded. Required actions remain native controls in the task area. A progress indicator does not create a second action with the same purpose.

The account workspace uses 28px desktop shell gutters, reducing to 18px at 760px and 16px at 440px. Its two-column sign-in layout collapses at 760px. Institution lists and audit pagination adapt at 600px. The Read help page uses a 240px contents rail plus flexible reading column with a 32px gap; at 760px it stacks and the contents links become two columns. At 430px, its outer gutters reduce to 8px. Tutorial images are bounded to their actual 390px reading width.

| Query boundary | Actual retained behavior |
| --- | --- |
| Workspace max 350px / max 359px | Narrow plant-picker wrapping and compact progress/tool arrangements retained from existing rules. |
| Guide max 430px | Tighter outer and reading gutters. |
| Account max 440px | Single-column grids, smaller heading and full-width action layout. |
| Workspace max 599px / min 600px | Phone task layout versus wider progress, summary and approval layout. |
| Account/list max 600px | Plant-list and audit controls wrap into phone layout. |
| Account/help max 760px | Account split and help rail stack. |
| Workspace min 960px | Existing wide-layout rules remain; later review and calibration rules govern the current single-photo task layout. |

These are CSS boundaries, not evidence of tests on corresponding physical devices. Design for reflow from 320px through desktop. Keep buttons, navigation links, disclosure summaries and labeled checkbox rows at least 44px high; workspace fields are 46px and account buttons/fields are 46px. The native checkbox glyph may be smaller inside its 44px row; leaf-table checkboxes are 44px themselves. Larger primary controls retain their existing 48-52px minima. Safe-area insets and viewport-height bounds protect image and dialog space.

## Elevation & Depth

Work surfaces and buttons are flat: no ambient card shadows, gradients or hover lifts. Sections use white fill, spacing and selective rules for grouping. The actual modal exception uses `0 12px 40px #172b2526` with backdrop `#142b254d`. The image brush cursor uses its existing one-pixel black/white outline for visibility over a photograph; it is an editing affordance, not decorative depth.

There is no new motion scale. The guide permits smooth anchor scrolling only when reduced motion is not requested. The workspace disables animation and transitions under reduced motion. Shared styles also force automatic scrolling under that preference.

**The Flat Surface Rule.** Keep ordinary task sections and actions flat; modal separation and image-tool visibility are the existing functional exceptions.

## Shapes

Controls use the shared gently rounded 8px corner. Workspace sections are square and borderless; ready-photo, results, diagnostics and dialogs retain their bounded 8px treatment. Photos use subtle 4px corners. Review badges use 4px corners; account membership pills retain 5px and account navigation 6px. The help reading container retains its 14px corner. These are intentional surface differences rather than a mandate to round every section.

## Components

### Actions

The actual task action owns emphasis: choose a plant, choose a photo, save scale, analyze, review, approve or open a result as dictated by real state. Workspace primary, secondary and quiet variants use the frontmatter assignments. Secondary and quiet hover surfaces use the soft green fill. Selected tools use green text, a two-pixel green border and soft fill; the primary tool and photo-view selection use solid green with white text. Pressed state is exposed through `aria-pressed`, not inferred from hue.

Workspace primary disabled controls use the explicit disabled pair at full opacity; other workspace disabled controls use their existing `#edf0ed` / `#617268` pair. Account disabled buttons retain 0.55 opacity. Do not present a disabled control as a working next step: keep its real blocker and available recovery visible.

### Fields and focus

Fields retain native input behavior, a white fill, stronger field border, 46px minimum height and 16px text. Use an explicit label and plain error text. Shared focus is a three-pixel outline with a three-pixel offset. Current workspace button/input/select/summary and integrated navigation focus is a three-pixel outline with a two-pixel offset, as noted in Colors. Guidance outlines are separate from keyboard focus; they must not erase `:focus-visible`.

Photo selection uses one visible, labeled Choose photo control with the native picker behind it. Do not replace it with an ornamental drop zone or duplicate its action.

### Navigation and progress

Account navigation uses a small filled current-page surface; measurement navigation uses a green bottom rule. Both retain `aria-current="page"`. Skip links become visible on focus. The progress disclosure, task instruction and optional tips stay subordinate to the real control. Hiding tips must not hide required errors, scale safeguards or approval blockers.

### Review, approval and results

Keep the image viewport, Colored leaves / Original photo switch, active-tool message and labeled tool groups together. Image and canvas scaling must preserve native mask coordinates and leaf identities. The viewport is bounded with `svh` and minimum heights rather than stretching masks to a different measurement geometry.

Human approval remains explicit. Calibrated and pixel-only routes retain distinct language. A result shows the saved measurement and gives a direct path to its history; history keeps one measurement type and unit at a time and does not join incompatible calibration setups. Empty and single-measurement states explain the actual next action instead of displaying invented growth metrics.

### Status, notices and recovery

Retain live status and alert semantics from the implementation. Review badges describe a state; they are not clickable filters. Recovery sections use the pale error surface, explicit failure explanation and available retry or sign-in action. Diagnostics may expand without replacing the plain instruction. Keep entered data and reviewed work recoverable where the existing behavior supports it.

## Do's and Don'ts

### Do:

- **Do** reuse the shared palette, system font stack and observed control primitives across both static mounts.
- **Do** keep task controls, 44px minimum interactive rows and keyboard focus visible at narrow widths.
- **Do** pair scientific states with explicit language, measurement type and units.
- **Do** preserve human approval, native mask geometry, leaf identity and immutable saved history.
- **Do** use real, clearly labeled procedural fixtures when demonstrating a state, without presenting them as biological evidence.

### Don't:

- **Don't** add a second theme, decorative gradients, marketing imagery, external fonts or a frontend framework for visual consistency.
- **Don't** imply cm² from pixel-only analysis or describe projected visible area as true 3D leaf area.
- **Don't** fabricate measurements, growth history, biological accuracy, training promotion or institution data.
- **Don't** remove scale safeguards, approval blockers, tenant boundaries or recovery actions to simplify a screen.
- **Don't** label automated browser evidence as physical iPhone testing, an independent novice study or WCAG certification.
