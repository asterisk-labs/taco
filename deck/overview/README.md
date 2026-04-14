# TACO v3 — Overview Deck

Entry-level deck for the [TACO](http://asterisk.coop/taco) specification. Covers what TACO is, why it exists, and how the pipeline works — no spec details, no API, no code deep-dives.

## Slides

1. **Hero** — animated taco/earth morph, parallax starfield
2. **Problem** — why AI4EO datasets have no standard packaging
3. **Solution** — the contract model (σ structure + μ metadata)
4. **Layout** — how a TACO dataset looks on disk
5. **How It Works** — 4 sub-slides navigable with ←/→
   - COZ (cloud-optimized ZIP vs normal ZIP)
   - Parquet (metadata in memory, random access)
   - VSI (universal file pointers, chainable paths)
   - Data (PyTorch DataLoader integration)
6. **Get Started** — install, links, QR code

## Navigation

- **↑/↓** or **W/S** — move between slides
- **←/→** or **A/D** — move between sub-slides (How It Works)
- **D-pad** — bottom-right corner, clickable

## Quick start

Open `index.html` in a browser. No build step required.

## Structure

```
index.html
css/
  variables.css    → theme colors
  animations.css   → keyframes
  hero.css         → hero + nav d-pad
  labels.css       → floating labels
  content.css      → slides, typography, pipeline bar
js/
  main.js          → starfield canvas, parallax
  nav.js           → section loader, scroll-snap, sub-slides
sections/
  problem.html
  solution.html
  layout.html
  how-it-works.html
  get-started.html
assets/
  solution.svg     → animated contract validation
  layout.svg       → directory tree diagram
  coz.svg          → normal ZIP vs COZ comparison
  parquet.svg      → in-memory table with filter
  vsi.svg          → VSI path anatomy
  dataloader.svg   → PyTorch DataLoader pipeline animation
  qr.png           → QR code to asterisk.coop/taco/
```

## License

MIT