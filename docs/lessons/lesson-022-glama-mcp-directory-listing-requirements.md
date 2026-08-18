---
id: lesson-022-glama-mcp-directory-listing-requirements
type: lesson
status: active
created: "2026-03-10"
owner: manu
tags: [hive, lesson, distribution, mcp, glama]
---

# Glama MCP Directory Listing Requirements

**Context:** Submitting hive-vault to MCP server directories for discoverability
**Problem:** punkpeye/awesome-mcp-servers requires a Glama listing link next to the GitHub link. Glama requires a glama.json in the repo root.
**Solution:** 1) Create glama.json with $schema and maintainers array. 2) Add Glama badge (PNG, 380x200) to README. 3) Update awesome-list PR entry with [glama](https://glama.ai/mcp/servers/OWNER/REPO) link after GitHub link. Schema: https://glama.ai/mcp/schemas/server.json — only required field is maintainers (GitHub usernames).
**Tags:** `#distribution` `#mcp` `#glama`
