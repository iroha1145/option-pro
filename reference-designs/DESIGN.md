---
name: Precision Fluidity
colors:
  surface: '#f8f9ff'
  surface-dim: '#cbdbf5'
  surface-bright: '#f8f9ff'
  surface-container-lowest: '#ffffff'
  surface-container-low: '#eff4ff'
  surface-container: '#e5eeff'
  surface-container-high: '#dce9ff'
  surface-container-highest: '#d3e4fe'
  on-surface: '#0b1c30'
  on-surface-variant: '#464554'
  inverse-surface: '#213145'
  inverse-on-surface: '#eaf1ff'
  outline: '#777586'
  outline-variant: '#c7c4d7'
  surface-tint: '#5148d7'
  primary: '#2a14b4'
  on-primary: '#ffffff'
  primary-container: '#4338ca'
  on-primary-container: '#c1beff'
  inverse-primary: '#c3c0ff'
  secondary: '#006c49'
  on-secondary: '#ffffff'
  secondary-container: '#6cf8bb'
  on-secondary-container: '#00714d'
  tertiary: '#770927'
  on-tertiary: '#ffffff'
  tertiary-container: '#97253d'
  on-tertiary-container: '#ffafb6'
  error: '#ba1a1a'
  on-error: '#ffffff'
  error-container: '#ffdad6'
  on-error-container: '#93000a'
  primary-fixed: '#e3dfff'
  primary-fixed-dim: '#c3c0ff'
  on-primary-fixed: '#100069'
  on-primary-fixed-variant: '#372abf'
  secondary-fixed: '#6ffbbe'
  secondary-fixed-dim: '#4edea3'
  on-secondary-fixed: '#002113'
  on-secondary-fixed-variant: '#005236'
  tertiary-fixed: '#ffdadc'
  tertiary-fixed-dim: '#ffb2b9'
  on-tertiary-fixed: '#400010'
  on-tertiary-fixed-variant: '#891933'
  background: '#f8f9ff'
  on-background: '#0b1c30'
  surface-variant: '#d3e4fe'
typography:
  display:
    fontFamily: Inter
    fontSize: 48px
    fontWeight: '700'
    lineHeight: '1.1'
    letterSpacing: -0.02em
  headline-lg:
    fontFamily: Inter
    fontSize: 32px
    fontWeight: '600'
    lineHeight: '1.2'
    letterSpacing: -0.01em
  headline-lg-mobile:
    fontFamily: Inter
    fontSize: 24px
    fontWeight: '600'
    lineHeight: '1.2'
  headline-md:
    fontFamily: Inter
    fontSize: 20px
    fontWeight: '600'
    lineHeight: '1.3'
  body-lg:
    fontFamily: Inter
    fontSize: 16px
    fontWeight: '400'
    lineHeight: '1.6'
  body-md:
    fontFamily: Inter
    fontSize: 14px
    fontWeight: '400'
    lineHeight: '1.5'
  label-sm:
    fontFamily: Inter
    fontSize: 12px
    fontWeight: '600'
    lineHeight: '1'
    letterSpacing: 0.02em
  data-mono:
    fontFamily: Inter
    fontSize: 14px
    fontWeight: '500'
    lineHeight: '1'
rounded:
  sm: 0.25rem
  DEFAULT: 0.5rem
  md: 0.75rem
  lg: 1rem
  xl: 1.5rem
  full: 9999px
spacing:
  base: 4px
  xs: 8px
  sm: 12px
  md: 16px
  lg: 24px
  xl: 32px
  gutter: 20px
  margin-mobile: 16px
  margin-desktop: 32px
---

## Brand & Style

The design system is engineered for the modern retail trader, balancing high-stakes financial precision with an approachable, "natural" aesthetic. It moves away from the aggressive, dark-mode "gamer" aesthetic common in trading platforms, favoring a clean, light-filled environment that reduces cognitive load during high-volatility sessions.

The style is a blend of **Modern Minimalism** and **Tactile Layering**. It utilizes a sophisticated "Soft White" foundation to allow data-rich visualizations to breathe. The emotional response is one of calm confidence—professional enough for serious capital management, yet vibrant enough to feel contemporary and tech-forward.

## Colors

The palette is rooted in a "natural" spectrum.

- **Primary (Deep Indigo):** Used for core actions, AI-driven insights, and primary navigation. It provides a stable, authoritative anchor.
- **Success (Mint Green):** A fresh, high-visibility green for "up" movements, gains, and bullish indicators.
- **Danger (Coral Red):** A soft yet urgent red for losses and bearish indicators, designed to be legible without causing visual fatigue.
- **Neutral/Base:** A range of slate grays and cool whites (Zinc/Slate) provides the structural framework, ensuring that the interface feels "airy" despite the high data density.

## Typography

This design system relies on **Inter** for its exceptional legibility in data-heavy environments. The typographic scale is tightly controlled to maintain hierarchy in complex dashboards.

Critical to this system is the use of **tabular figures** (monospaced numbers) for all price movements and Greeks (Delta, Gamma, etc.). This ensures that numbers align vertically in tables, allowing traders to scan and compare values instantly without the "jitter" of proportional fonts. Headlines use tighter tracking and heavier weights to feel "youthful" and impactful, while body copy remains open for readability.

## Layout & Spacing

The layout follows a **Fluid Grid** model with high-density content zones.

- **Desktop:** A 12-column grid with 20px gutters. Main data tables and charts typically span 8-9 columns, with a 3-4 column "Action/Detail" sidebar on the right.
- **Density:** We utilize "Functional Whitespace." While the data is dense, margins around container cards are kept generous (24px+) to prevent the interface from feeling cramped.
- **Reflow:** On smaller viewports, the sidebar stacks beneath the primary chart/table. On mobile, the grid collapses to a single column with horizontal scrolling enabled specifically for data tables to preserve data integrity.

## Elevation & Depth

Hierarchy is achieved through **Tonal Layering** supplemented by **Ambient Shadows**.

The background uses a subtle off-white (`#F8FAFC`). Components sit on "Level 1" white surfaces (`#FFFFFF`). To create a youthful, layered look:
1.  **Low Elevation:** Cards use a very soft, diffused shadow (0px 4px 20px rgba(0,0,0,0.04)) to appear lifted from the background.
2.  **Interactive States:** On hover, buttons and cards increase their shadow slightly (0px 8px 30px rgba(0,0,0,0.08)) and may show a 1px primary-colored border.
3.  **Modals/Overlays:** Use a backdrop blur (Glassmorphism) of 8px with a 40% white tint to maintain the "airy" feel while focusing the user's attention.

## Shapes

The shape language is "Soft-Modern."

- **Base Radius:** 8px (0.5rem) for small components like inputs and small buttons.
- **Large Radius:** 16px (1rem) for main dashboard cards and container modules.
- **Pill Shapes:** Used exclusively for status indicators (e.g., "In the Money" chips) and toggle switches to provide a distinct visual contrast against the more structured rectangular cards.

## Components

### Buttons & Inputs
Buttons use a 12px height padding for a substantial, tactile feel. The Primary Button is Deep Indigo with white text. Input fields are minimalist: 1px light gray border that transitions to Indigo on focus, with a subtle inner shadow to suggest depth.

### Data Chips
Used for tickers (e.g., $SPY, $AAPL) and option strikes. These are semi-transparent versions of the success/danger colors (e.g., 10% opacity Mint Green background with 100% opacity Mint Green text) to keep the UI light.

### Data Tables (The Core)
High-density rows with 1px border-bottom separators. Every other row has a faint gray tint for easier horizontal scanning. Headers are in `label-sm` (all caps) to distinguish them from the data.

### Option Chain Cards
Specialized cards that group "Calls" and "Puts" around a central "Strike Price" column. These use the 16px corner radius and include a subtle "glass" effect on the header to denote the current underlying stock price.

### Charts
Candlestick or line charts should use the Mint Green and Coral Red for indicators. The background grid lines of charts should be ultra-faint (#F1F5F9) to keep the focus on the price action.
