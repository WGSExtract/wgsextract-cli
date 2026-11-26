# WGS Extract icons

Use these icons for genomics concepts. Keep general controls such as Settings,
Download and Delete in the platform's regular icon set.

- Draw on a 24 × 24 grid with a 1.75-unit stroke and round caps and joins.
- Paint with `currentColor` (or `none`) so the surrounding UI sets the color.
  Prefer a recognizable silhouette to extra detail at 16 px. Tune weight by
  adjusting the paths, not the stroke width.
- Copy the root from an existing SVG and use self-closing `path`, `rect`,
  `circle` or `ellipse` elements. The app build checks the files before turning
  them into inline icons. Icon maps select these shipped names; they don't load
  SVG markup or paths from third-party bundles.

To add one, save it here with a hyphenated lowercase name, map the semantic
`iconName` to its filename stem in the bundle's `[wgse]` table, and provide an
`[emoji]` mapping (or a `textIcon`) for emoji mode. Run `just gui-ci` and
`python3 gui/mockups/icons/build_icons.py`, then check the icon at 16, 20 and
24 px in light and dark themes.
