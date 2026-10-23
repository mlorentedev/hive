---
id: lesson-111-a-site-base-does-not-reach-markdown-links
type: lesson
status: active
created: "2026-10-22"
owner: manu
tags: [hive, lesson, astro, starlight, pages, docs, HIVE-519]
---

# A site base does not reach Markdown links

**Context:** The docs site sets `base: '/hive'` in `site/astro.config.mjs`
because GitHub Pages serves it at `mlorentedev.github.io/hive/`. Starlight
applies the base to everything it generates: sidebar, assets, locale switcher,
search.

**Problem:** It does not rewrite absolute links written in Markdown.
`[guide](/configuration/)` ships as `href="/configuration/"`, which resolves to
the user site root and 404s. 22 such links (11 per locale) built green, because
`astro build` does not check links and the dev server serves them fine at the
root.

**Solution:** Prefix every internal Markdown link with `/hive/`, matching the
48 that already had it, and scan the built `dist/` in the `pages` build
(`npm run check:links`, `site/scripts/check-links.mjs`). Every root-relative
`href`/`src` must carry the base and resolve to a built file, so a renamed page
fails too. Scanning `dist/` rather than the sources also covers frontmatter
`link:` values, MDX components and templates. `tests/test_pages_workflow.py`
pins the step after the build and before the artifact upload.

**Rule:** A framework's `base` option covers the URLs the framework emits, not
the ones you typed. Check links in the built output, where both meet.
