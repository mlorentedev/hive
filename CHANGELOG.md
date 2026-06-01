# Changelog

## [1.26.1](https://github.com/mlorentedev/hive/compare/v1.26.0...v1.26.1) (2026-06-01)


### Documentation

* **spec:** record Phase C activation/rollout as the exit criterion (HIVE-118) ([#177](https://github.com/mlorentedev/hive/issues/177)) ([8b94358](https://github.com/mlorentedev/hive/commit/8b94358d070f28634e18ac003fa2f84fed50454f))

## [1.26.0](https://github.com/mlorentedev/hive/compare/v1.25.1...v1.26.0) (2026-06-01)


### Features

* cross-session metrics core + token-gated /status endpoint (HIVE-118) ([#172](https://github.com/mlorentedev/hive/issues/172)) ([5c288b0](https://github.com/mlorentedev/hive/commit/5c288b0f90c61c821960394a1daa774500aa4ce9))

## [1.25.1](https://github.com/mlorentedev/hive/compare/v1.25.0...v1.25.1) (2026-06-01)


### Bug Fixes

* harden hive client shim against a wedged daemon (HIVE-118) ([#169](https://github.com/mlorentedev/hive/issues/169)) ([0e033d1](https://github.com/mlorentedev/hive/commit/0e033d1819ceb45808493f5d7b97d98cbb8c4123))

## [1.25.0](https://github.com/mlorentedev/hive/compare/v1.24.0...v1.25.0) (2026-06-01)


### Features

* add `hive client` stdio shim that proxies to the daemon (HIVE-118) ([#167](https://github.com/mlorentedev/hive/issues/167)) ([619e656](https://github.com/mlorentedev/hive/commit/619e65610088fe3290fa177c995e337db6bc7adc))

## [1.24.0](https://github.com/mlorentedev/hive/compare/v1.23.1...v1.24.0) (2026-05-31)


### Features

* add `hive serve` daemon entrypoint (HIVE-118) ([#164](https://github.com/mlorentedev/hive/issues/164)) ([3494bfb](https://github.com/mlorentedev/hive/commit/3494bfb3f4a1539ece9322be7351716f45676fb7))

## [1.23.1](https://github.com/mlorentedev/hive/compare/v1.23.0...v1.23.1) (2026-05-31)


### Bug Fixes

* harden scope configuration against misconfiguration ([#161](https://github.com/mlorentedev/hive/issues/161)) ([b1daed8](https://github.com/mlorentedev/hive/commit/b1daed8464f96482a2552921f6140cd58f02588c))

## [1.23.0](https://github.com/mlorentedev/hive/compare/v1.22.0...v1.23.0) (2026-05-31)


### Features

* add agents scope for AI-agent inboxes ([#154](https://github.com/mlorentedev/hive/issues/154)) ([6bc4412](https://github.com/mlorentedev/hive/commit/6bc441222bb0b032cf8793583d33d3178df1b5d9))

## [1.22.0](https://github.com/mlorentedev/hive/compare/v1.21.1...v1.22.0) (2026-05-31)


### Features

* **tools:** accept wrong param-name aliases + tighten docstrings ([#151](https://github.com/mlorentedev/hive/issues/151)) ([#152](https://github.com/mlorentedev/hive/issues/152)) ([6d29633](https://github.com/mlorentedev/hive/commit/6d29633ffe40c783055bf6e67295a99fd7cc08a5))

## [1.21.1](https://github.com/mlorentedev/hive/compare/v1.21.0...v1.21.1) (2026-05-29)


### Documentation

* migrate build/operate knowledge into docs/ (KPM-009) ([#149](https://github.com/mlorentedev/hive/issues/149)) ([8675017](https://github.com/mlorentedev/hive/commit/867501741529e7830b0adf9fdbfeb7b1f4b0db05))

## [1.21.0](https://github.com/mlorentedev/hive/compare/v1.20.1...v1.21.0) (2026-05-28)


### Features

* **hive:** cross-OS CI matrix for filelock eviction + archive HIVE-116 spec (HIVE-116 PR-3) ([8cb35e0](https://github.com/mlorentedev/hive/commit/8cb35e0b7c6fd430d53feb5437f591fa9a185e9c))

## [1.20.1](https://github.com/mlorentedev/hive/compare/v1.20.0...v1.20.1) (2026-05-28)


### Bug Fixes

* **hive:** evict cooperative filelock on deadline (HIVE-116 PR-2) ([#144](https://github.com/mlorentedev/hive/issues/144)) ([845c830](https://github.com/mlorentedev/hive/commit/845c830ebb15b99ccc5eead974d3d640b9332166))

## [1.20.0](https://github.com/mlorentedev/hive/compare/v1.19.1...v1.20.0) (2026-05-28)


### Features

* **hive:** observable partial-state writes (HIVE-116 PR-1) ([#142](https://github.com/mlorentedev/hive/issues/142)) ([672473f](https://github.com/mlorentedev/hive/commit/672473fa2140f5f1a11b1011b8ffb96b3208fd43))

## [1.19.1](https://github.com/mlorentedev/hive/compare/v1.19.0...v1.19.1) (2026-05-23)


### Documentation

* **site:** add OpenCode + unify implementation examples as tabs (EN+ES) ([#128](https://github.com/mlorentedev/hive/issues/128)) ([b9e1940](https://github.com/mlorentedev/hive/commit/b9e1940d4a5ba7ebbf7c6b6f0f6e6d4654638687))

## [1.19.0](https://github.com/mlorentedev/hive/compare/v1.18.0...v1.19.0) (2026-05-23)


### Features

* **hive:** HIVE-115 PR-4 Outbox + Reconciler + detect-and-defer (ADR-009 v2, ADR-010) ([#121](https://github.com/mlorentedev/hive/issues/121)) ([2e1a5b0](https://github.com/mlorentedev/hive/commit/2e1a5b0a28613c08dd10cef3a489b84e7b7be1da)), closes [#110](https://github.com/mlorentedev/hive/issues/110)

## [1.18.0](https://github.com/mlorentedev/hive/compare/v1.17.0...v1.18.0) (2026-05-23)


### Features

* **hive:** HIVE-115 PR-3 bounded_call hard deadline ([#111](https://github.com/mlorentedev/hive/issues/111), ADR-008) ([#119](https://github.com/mlorentedev/hive/issues/119)) ([43a6c4a](https://github.com/mlorentedev/hive/commit/43a6c4ac3e8294f3ff9ca129a1e6de70c42fb1ec))

## [1.17.0](https://github.com/mlorentedev/hive/compare/v1.16.0...v1.17.0) (2026-05-22)


### Features

* **hive:** HIVE-115 capture_lesson XML-leak defense ([#114](https://github.com/mlorentedev/hive/issues/114) Tier-1) ([#117](https://github.com/mlorentedev/hive/issues/117)) ([5b01bb9](https://github.com/mlorentedev/hive/commit/5b01bb9903f34d05522bc703ceaa6ca7eed38136))

## [1.16.0](https://github.com/mlorentedev/hive/compare/v1.15.0...v1.16.0) (2026-05-22)


### Features

* **hive:** HIVE-115 Phase A defensive — WAL drain + telemetry + tunable lock + docs ([#115](https://github.com/mlorentedev/hive/issues/115)) ([f92e040](https://github.com/mlorentedev/hive/commit/f92e0406233982d99553055a5fe4ffec23df4e1d))

## [1.15.0](https://github.com/mlorentedev/hive/compare/v1.14.1...v1.15.0) (2026-05-22)


### Features

* **hive:** HIVE-109 vault_health server identity + opt-in runtime metadata ([#112](https://github.com/mlorentedev/hive/issues/112)) ([060fbc3](https://github.com/mlorentedev/hive/commit/060fbc38abf593f510e1cd8e6adc893731755f86))

## [1.14.1](https://github.com/mlorentedev/hive/compare/v1.14.0...v1.14.1) (2026-05-21)


### Documentation

* post-merge cleanup for commit-policy + ghost-response (HIVE-104) ([#107](https://github.com/mlorentedev/hive/issues/107)) ([277702f](https://github.com/mlorentedev/hive/commit/277702f91b2cd6544f6e4d71aaefe94aa3130f13))

## [1.14.0](https://github.com/mlorentedev/hive/compare/v1.13.0...v1.14.0) (2026-05-21)


### Features

* **hive:** HIVE-104 write throughput (commit coalescer + observable shim + opt-in batching) ([#104](https://github.com/mlorentedev/hive/issues/104)) ([953b608](https://github.com/mlorentedev/hive/commit/953b60837b24334130aaa57010b2b7489dbcf552))

## [1.13.0](https://github.com/mlorentedev/hive/compare/v1.12.10...v1.13.0) (2026-05-20)


### Features

* lesson reinforcement counter with confidence decay (HIVE-97) ([#98](https://github.com/mlorentedev/hive/issues/98)) ([e37c7e2](https://github.com/mlorentedev/hive/commit/e37c7e2b5ca0c9c77a0513c630e0102280d88cb2))

## [1.12.10](https://github.com/mlorentedev/hive/compare/v1.12.9...v1.12.10) (2026-05-19)


### Bug Fixes

* **vault_health:** resolve cross-scope and project-prefixed wikilinks ([#94](https://github.com/mlorentedev/hive/issues/94)) ([#95](https://github.com/mlorentedev/hive/issues/95)) ([2c94e1c](https://github.com/mlorentedev/hive/commit/2c94e1c8e9bc6d8f2f3b51662dd6f1b161741b8f))

## [1.12.9](https://github.com/mlorentedev/hive/compare/v1.12.8...v1.12.9) (2026-05-18)


### Bug Fixes

* post-PR-90 stability pass — correctness, perf, contention, UX ([#92](https://github.com/mlorentedev/hive/issues/92)) ([20a869c](https://github.com/mlorentedev/hive/commit/20a869c3b1f8356710090cb4a46a332f8d9c2a2c))

## [1.12.8](https://github.com/mlorentedev/hive/compare/v1.12.7...v1.12.8) (2026-05-18)


### Bug Fixes

* prevent multi-process hangs, crashes, and write-loss ([#90](https://github.com/mlorentedev/hive/issues/90)) ([8e6cb8b](https://github.com/mlorentedev/hive/commit/8e6cb8b70cfe645c1d2e805968df8b67c392c3f7))

## [1.12.7](https://github.com/mlorentedev/hive/compare/v1.12.6...v1.12.7) (2026-05-18)


### Bug Fixes

* **release:** keep uv.lock self-reference in sync with project version ([#88](https://github.com/mlorentedev/hive/issues/88)) ([29dea3b](https://github.com/mlorentedev/hive/commit/29dea3b16f3cdd50395cb19a27ecdfd145a51cf1))

## [1.12.6](https://github.com/mlorentedev/hive/compare/v1.12.5...v1.12.6) (2026-05-18)


### Documentation

* clean up README — remove resolved bug notice and refresh counts ([#86](https://github.com/mlorentedev/hive/issues/86)) ([ae72111](https://github.com/mlorentedev/hive/commit/ae72111cb0a40e7c800734c473f41e8d4287a0f9))

## [1.12.5](https://github.com/mlorentedev/hive/compare/v1.12.4...v1.12.5) (2026-05-18)


### Documentation

* add codecov badge to README ([#84](https://github.com/mlorentedev/hive/issues/84)) ([ccec00a](https://github.com/mlorentedev/hive/commit/ccec00ac7a8ca0ab6602b15b7e728c66c6f5e660))

## [1.12.4](https://github.com/mlorentedev/hive/compare/v1.12.3...v1.12.4) (2026-05-16)


### Documentation

* document HIVE_LOG_LEVEL env var and refresh counts ([#82](https://github.com/mlorentedev/hive/issues/82)) ([49b3ec3](https://github.com/mlorentedev/hive/commit/49b3ec3c986529a7afdef452cb4df86c26375d24))

## [1.12.3](https://github.com/mlorentedev/hive/compare/v1.12.2...v1.12.3) (2026-05-16)


### Bug Fixes

* **ci:** compare workflow_dispatch boolean input as boolean ([#79](https://github.com/mlorentedev/hive/issues/79)) ([46fd32e](https://github.com/mlorentedev/hive/commit/46fd32e6d177ceebfb8e17818af212785af92c7f))

## [1.12.2](https://github.com/mlorentedev/hive/compare/v1.12.1...v1.12.2) (2026-05-15)


### Bug Fixes

* prevent stdio transport disconnect after request cancellation ([#75](https://github.com/mlorentedev/hive/issues/75)) ([#76](https://github.com/mlorentedev/hive/issues/76)) ([a5e6372](https://github.com/mlorentedev/hive/commit/a5e637242c901aa11ddb9788e7cd62c9a72309db))

## [1.12.1](https://github.com/mlorentedev/hive/compare/v1.12.0...v1.12.1) (2026-05-12)


### Bug Fixes

* stop false positives in vault_health link and frontmatter validator ([#70](https://github.com/mlorentedev/hive/issues/70)) ([03becde](https://github.com/mlorentedev/hive/commit/03becde013b7fffa20f42784a94779cb5ad19af0))

## [1.12.0](https://github.com/mlorentedev/hive/compare/v1.11.6...v1.12.0) (2026-03-27)


### Features

* hierarchical scope support for work vault ([#67](https://github.com/mlorentedev/hive/issues/67)) ([63f2378](https://github.com/mlorentedev/hive/commit/63f2378034db282bc301c4108d381e3c36da9fea))

## [1.11.6](https://github.com/mlorentedev/hive/compare/v1.11.5...v1.11.6) (2026-03-14)


### Bug Fixes

* add tool-level timeouts to prevent MCP tool hangs ([#64](https://github.com/mlorentedev/hive/issues/64)) ([f15b741](https://github.com/mlorentedev/hive/commit/f15b7415c52fb109f373646402313af850967c0a))

## [1.11.5](https://github.com/mlorentedev/hive/compare/v1.11.4...v1.11.5) (2026-03-13)


### Bug Fixes

* **site:** remove broken ADR-004 links in architecture docs ([c87f5bf](https://github.com/mlorentedev/hive/commit/c87f5bfa9e63b1b5ec224c6becf3be0734c39d88))

## [1.11.4](https://github.com/mlorentedev/hive/compare/v1.11.3...v1.11.4) (2026-03-13)


### Bug Fixes

* thread-safe SQLite trackers and vault write serialization ([#60](https://github.com/mlorentedev/hive/issues/60)) ([c11b7e2](https://github.com/mlorentedev/hive/commit/c11b7e2779166fb856532ab04e56195a0f71b9bd))

## [1.11.3](https://github.com/mlorentedev/hive/compare/v1.11.2...v1.11.3) (2026-03-13)


### Bug Fixes

* harden vault tools against encoding errors and path traversal ([#57](https://github.com/mlorentedev/hive/issues/57)) ([edcd3c0](https://github.com/mlorentedev/hive/commit/edcd3c087bb253ea2ba0af8c5e895f98eb4d213f))

## [1.11.2](https://github.com/mlorentedev/hive/compare/v1.11.1...v1.11.2) (2026-03-13)


### Bug Fixes

* **config:** accept VAULT_PATH env var and validate vault existence ([#55](https://github.com/mlorentedev/hive/issues/55)) ([ff21fbd](https://github.com/mlorentedev/hive/commit/ff21fbd834936febc635919964fe18783599c916))

## [1.11.1](https://github.com/mlorentedev/hive/compare/v1.11.0...v1.11.1) (2026-03-12)


### Bug Fixes

* **vault_patch:** tolerant matching for read→patch workflow ([#53](https://github.com/mlorentedev/hive/issues/53)) ([bb9b9a7](https://github.com/mlorentedev/hive/commit/bb9b9a773e194cf9d6db4d9613137eb9b0926f18)), closes [#52](https://github.com/mlorentedev/hive/issues/52)

## [1.11.0](https://github.com/mlorentedev/hive/compare/v1.10.0...v1.11.0) (2026-03-10)


### Features

* **site:** add Spanish i18n and landing copy ([#49](https://github.com/mlorentedev/hive/issues/49)) ([988a619](https://github.com/mlorentedev/hive/commit/988a6195181db8c336ed6a3697bf7089821d33aa))

## [1.10.0](https://github.com/mlorentedev/hive/compare/v1.9.1...v1.10.0) (2026-03-10)


### Features

* add Dockerfile and CI smoke test ([#47](https://github.com/mlorentedev/hive/issues/47)) ([46d1b20](https://github.com/mlorentedev/hive/commit/46d1b208844a8794b7f89da2346cf1417a4abf78))

## [1.9.1](https://github.com/mlorentedev/hive/compare/v1.9.0...v1.9.1) (2026-03-09)


### Bug Fixes

* resolve all audit findings from tool consolidation ([#44](https://github.com/mlorentedev/hive/issues/44)) ([417934a](https://github.com/mlorentedev/hive/commit/417934a9cd21d0c50a0ec15c44fccab7c5bd1e10))

## [1.9.0](https://github.com/mlorentedev/hive/compare/v1.8.0...v1.9.0) (2026-03-09)


### Features

* consolidate 19 MCP tools into 10 ([#42](https://github.com/mlorentedev/hive/issues/42)) ([d7ac3b5](https://github.com/mlorentedev/hive/commit/d7ac3b56bfd3ec8504810926f2e8f76d5ef1fbad))

## [1.8.0](https://github.com/mlorentedev/hive/compare/v1.7.2...v1.8.0) (2026-03-08)


### Features

* improve server instructions for universal tool adoption ([#40](https://github.com/mlorentedev/hive/issues/40)) ([5ad8474](https://github.com/mlorentedev/hive/commit/5ad84744173ddb2e4b9236687eb11eb16ec19e4d))

## [1.7.2](https://github.com/mlorentedev/hive/compare/v1.7.1...v1.7.2) (2026-03-08)


### Documentation

* add hook automation examples for session briefing ([#38](https://github.com/mlorentedev/hive/issues/38)) ([d8312b4](https://github.com/mlorentedev/hive/commit/d8312b457d828e261583f960bb1d9280a909550b))

## [1.7.1](https://github.com/mlorentedev/hive/compare/v1.7.0...v1.7.1) (2026-03-08)


### Documentation

* update architecture diagram and server instructions ([#35](https://github.com/mlorentedev/hive/issues/35)) ([95de6fa](https://github.com/mlorentedev/hive/commit/95de6fa6b4cb06b9706277fa9c740e0a483adf99))

## [1.7.0](https://github.com/mlorentedev/hive/compare/v1.6.0...v1.7.0) (2026-03-07)


### Features

* add vault_validate tool for drift detection ([#33](https://github.com/mlorentedev/hive/issues/33)) ([0ec4bf5](https://github.com/mlorentedev/hive/commit/0ec4bf59c08162a780b8ceee426223ece5ea61b3))

## [1.6.0](https://github.com/mlorentedev/hive/compare/v1.5.1...v1.6.0) (2026-03-07)


### Features

* add extract_lessons tool for worker-powered lesson extraction ([#31](https://github.com/mlorentedev/hive/issues/31)) ([37c0173](https://github.com/mlorentedev/hive/commit/37c0173fa57bfcd03affff35a7c0f58ab2d713b3))

## [1.5.1](https://github.com/mlorentedev/hive/compare/v1.5.0...v1.5.1) (2026-03-07)


### Bug Fixes

* harden MCP server stability and security ([#29](https://github.com/mlorentedev/hive/issues/29)) ([3388d6d](https://github.com/mlorentedev/hive/commit/3388d6df99dcc955e3a864b682598c831adf248e))

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
