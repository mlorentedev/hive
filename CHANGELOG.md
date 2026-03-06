# Changelog

## [1.5.0](https://github.com/mlorentedev/hive/compare/v1.4.5...v1.5.0) (2026-03-06)


### Features

* add multi-replacement support to vault_patch ([#25](https://github.com/mlorentedev/hive/issues/25)) ([5fcfc85](https://github.com/mlorentedev/hive/commit/5fcfc85cc92a9fa7ced1f444e46c7b6106d08780))

## [1.4.5](https://github.com/mlorentedev/hive/compare/v1.4.4...v1.4.5) (2026-03-06)


### Bug Fixes

* **site:** correct light/dark theme accent colors ([#22](https://github.com/mlorentedev/hive/issues/22)) ([f7cf021](https://github.com/mlorentedev/hive/commit/f7cf021a2a445524f0011472bcb3190afd729e7b))

## [1.4.4](https://github.com/mlorentedev/hive/compare/v1.4.3...v1.4.4) (2026-03-06)


### Documentation

* Obsidian branding + recommended workflow + tabbed landing install ([#20](https://github.com/mlorentedev/hive/issues/20)) ([945de00](https://github.com/mlorentedev/hive/commit/945de00f2879726d7ca6526f4b04acb707a27fa2))

## [1.4.3](https://github.com/mlorentedev/hive/compare/v1.4.2...v1.4.3) (2026-03-06)


### Documentation

* add multi-client MCP setup (Claude, Gemini, Codex) ([b78a866](https://github.com/mlorentedev/hive/commit/b78a866c24532c6dcff3e6d8c75b8d902a7a74e9))
* landing page overhaul — multi-client tabs, troubleshooting, model rationale ([#19](https://github.com/mlorentedev/hive/issues/19)) ([2a60e9e](https://github.com/mlorentedev/hive/commit/2a60e9ed22a9a4fda8bd3768fe5397b8e0ac7693))

## [1.4.2](https://github.com/mlorentedev/hive/compare/v1.4.1...v1.4.2) (2026-03-06)


### Documentation

* **site:** update landing page for v1.3.0 ([188511e](https://github.com/mlorentedev/hive/commit/188511efdcdd1adaf164b56ca3f9f160538f18a8))

## [1.4.1](https://github.com/mlorentedev/hive/compare/v1.4.0...v1.4.1) (2026-03-06)


### Bug Fixes

* security hardening + code audit fixes ([#15](https://github.com/mlorentedev/hive/issues/15)) ([a7eb750](https://github.com/mlorentedev/hive/commit/a7eb750c91d3ae6e17741eb8b0b0bcbe3ebffe2a))

## [1.4.0](https://github.com/mlorentedev/hive/compare/v1.3.0...v1.4.0) (2026-03-06)


### Features

* add vault_list_files, vault_patch, and regex search ([#13](https://github.com/mlorentedev/hive/issues/13)) ([16425b9](https://github.com/mlorentedev/hive/commit/16425b9c6175c43f704063f942b1cf27df560f48))

## [1.3.0](https://github.com/mlorentedev/hive/compare/v1.2.0...v1.3.0) (2026-03-05)


### Features

* parametrization audit — extract hardcoded values to HiveSettings ([5a0cb02](https://github.com/mlorentedev/hive/commit/5a0cb020867aaaab9a3e6708344fedb6ce9a9891))

## [1.2.0](https://github.com/mlorentedev/hive/compare/v1.1.0...v1.2.0) (2026-03-05)


### Features

* add capture_lesson tool for inline lesson extraction (P2) ([100c712](https://github.com/mlorentedev/hive/commit/100c712e48137bbe29ee4c8075239dae2eca1c61))
* benchmarking suite + lower default budget to $1/mo ([6754c16](https://github.com/mlorentedev/hive/commit/6754c1640c18b7989e4a1799dd570f4587781a06))
* configurable paid model + auto-upgrade MCP registration ([6b5e06d](https://github.com/mlorentedev/hive/commit/6b5e06d0eada7ff3f627304c8e4bc1cf9c48d720))
* increase vault_search/smart_search max_lines default to 500 ([73726a3](https://github.com/mlorentedev/hive/commit/73726a3b17194512ea30df99da8268b1dafec1dc))


### Documentation

* add benchmark characterization guide to site ([a1b47f6](https://github.com/mlorentedev/hive/commit/a1b47f684f38b4056d5208128e3da790a3aee412))
* add upgrade instructions for uvx users ([164b818](https://github.com/mlorentedev/hive/commit/164b8186f777527d8d0a816e60c5313ce4e5f77a))
* update site with paid model, budget, and benchmarks ([e69198b](https://github.com/mlorentedev/hive/commit/e69198bfc845581a6fc95f8377712cfb236d5294))

## [1.1.0](https://github.com/mlorentedev/hive/compare/v1.0.0...v1.1.0) (2026-03-05)


### Features

* adaptive session_briefing with relevance-based section ordering ([2b09947](https://github.com/mlorentedev/hive/commit/2b09947f5bd41c60716aec7d6fa9c210267ea3ff))
* add RelevanceTracker with EMA scoring, decay, and exploration ([42a053c](https://github.com/mlorentedev/hive/commit/42a053c2a3fdddb3cb26f098983f2f0da4bf687f))
* configurable vault scopes with auto-scan resolution ([e90365a](https://github.com/mlorentedev/hive/commit/e90365a31068ae66d85fbf5bc8ec7d9093822af7))


### Documentation

* add MCP activation guide and CLAUDE.md configuration best practices ([c80f4c8](https://github.com/mlorentedev/hive/commit/c80f4c8dbf2dec23fd7dd1e3d359d56a83aa7bc3))
* add prerequisites, use cases, and provider setup guides ([a5deaca](https://github.com/mlorentedev/hive/commit/a5deacae68a49ddaa8789f1f03a54989e00d9a16))

## [1.0.0](https://github.com/mlorentedev/hive/compare/v0.2.0...v1.0.0) (2026-03-04)


### ⚠ BREAKING CHANGES

* hive-vault and hive-worker CLI commands replaced by single hive command. hive-vault still works as an alias.

### Features

* add Astro Starlight landing page + GitHub Pages deployment ([bbf2b9a](https://github.com/mlorentedev/hive/commit/bbf2b9a06e6e73eeb79bcf13ea65ab4a79f3fd48))
* add end-to-end smoke tests for worker MCP server ([aa91264](https://github.com/mlorentedev/hive/commit/aa9126427ca42879c956a2b0850bc8e48fa435e2))
* Phase 3.0 — frontmatter parsing, metadata filters, stale detection ([efca6bc](https://github.com/mlorentedev/hive/commit/efca6bc875a61a16843b2f41ddb58452df9e38bf))
* Phase 3.1 — vault_summarize and vault_smart_search tools ([36381a2](https://github.com/mlorentedev/hive/commit/36381a2ce8158b20cd8d8d3e80814d6be1315fcc))
* Phase 3.2 — usage tracking, vault_usage tool, ADR-003 ([22f41ac](https://github.com/mlorentedev/hive/commit/22f41acffb5419db99f20f001c25a2d431821549))
* Phase 5 — MCP resources, session_briefing, vault_recent ([00d80a6](https://github.com/mlorentedev/hive/commit/00d80a65effc4695ce8b38fe4fd6d845ea58939a))
* unify vault + worker into single MCP server ([c8f561c](https://github.com/mlorentedev/hive/commit/c8f561c280d68a66c9bf2244cee6cf48068f23a1))


### Bug Fixes

* isolate openrouter_api_key test from environment ([b26ede4](https://github.com/mlorentedev/hive/commit/b26ede4df69b046273c84d62efb098eabe518505))
* move Path import to TYPE_CHECKING block in smoke tests ([5daf42e](https://github.com/mlorentedev/hive/commit/5daf42e824a752ac528bc7f669ae17e3ee5a0bcc))


### Documentation

* rewrite README with full API surface + add GitHub templates ([0851474](https://github.com/mlorentedev/hive/commit/0851474bbc610ee3b1953fbbc6df3c5eca8854e1))

## [0.2.0](https://github.com/mlorentedev/hive/compare/v0.1.0...v0.2.0) (2026-03-02)


### Features

* Worker MCP Server — task delegation with budget tracking ([#4](https://github.com/mlorentedev/hive/issues/4)) ([33b7bc3](https://github.com/mlorentedev/hive/commit/33b7bc3349431239ecdccb751ecb87b6d642559c))

## 0.1.0 (2026-03-02)


### Features

* add CLI entry point and release-please versioning ([91e8216](https://github.com/mlorentedev/hive/commit/91e821649812da5cc478b320893f9880fe9a6b8e))
* add project metadata and MIT license for PyPI readiness ([0d96411](https://github.com/mlorentedev/hive/commit/0d964111b1bbe4602052b8de9d8bfcae13639d9e))
* **ci:** automated PyPI publishing via trusted publishing ([e0ead9e](https://github.com/mlorentedev/hive/commit/e0ead9ec6217c5a1acace504a44150c0cf978f5f))
* close Phase 1.5 — integration tests, Makefile, coverage, CONTRIBUTING ([#3](https://github.com/mlorentedev/hive/issues/3)) ([b369b78](https://github.com/mlorentedev/hive/commit/b369b782433b4cc9fa1beb325f4e5ec80f6843a8))
* open source readiness — README + PyPI metadata ([#2](https://github.com/mlorentedev/hive/issues/2)) ([f2f7ba8](https://github.com/mlorentedev/hive/commit/f2f7ba83e344395f7309efb1bbf229c08a14eaf8))
* rename package to hive-vault for PyPI publication ([e648a2c](https://github.com/mlorentedev/hive/commit/e648a2c7d149962758c7f1d4edce919fb28fce86))
* vault MCP server with 6 tools + benchmark suite ([7a5bac3](https://github.com/mlorentedev/hive/commit/7a5bac3c416a7808bfb6372614fc27cb156ca3e0))


### Bug Fixes

* **ci:** use uv venv instead of --system for managed environments ([c2077b1](https://github.com/mlorentedev/hive/commit/c2077b14d425af74060d706bd3cc2fa02ff21e99))
