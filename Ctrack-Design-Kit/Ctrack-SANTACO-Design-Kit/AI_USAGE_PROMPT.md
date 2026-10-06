# Reusable AI implementation prompt

Use the attached Ctrack/SANTACO design kit as the visual source of truth for this project.

1. Import `tokens.css` or translate `colors.json` into the project's native theme system without changing the colour values.
2. Use Dark Blue `#002B49` and Teal `#40C1AC` as the dominant palette. Reserve Purple, Pink, Yellow and Orange for charts, statuses and data-source differentiation.
3. Use Red Hat Display for headings and Poppins for body text, with the supplied fallbacks when those fonts are unavailable.
4. Use the SVG files in `icons/` as functional interface icons. They use `currentColor`, so colour them through CSS rather than editing their paths.
5. Maintain data-source colours consistently: Ctrack live is teal, Bolt live is purple, CAN/mock is orange, and combined/modelled data is dark blue.
6. Use white cards, subtle blue-grey borders, low-elevation shadows and compact executive-dashboard spacing.
7. Never recolour, stretch, crop, redraw or add effects to `logos/ctrack-logo.png`.
8. Preserve accessible contrast, keyboard focus states, tooltip descriptions and text labels; colour must not be the only status indicator.
9. Clearly label live, simulated and modelled data. Never present mock data as a live API result.

Before finishing, verify desktop and mobile layouts and list every place where a supplied token or icon was used.

