---
type: Source Map
title: Job Boards Source Map
description: Maps the job-boards repository files, generated artifacts, tests, docs, automation, and OpenWiki pages to their engineering concepts.
tags: [source-map, navigation, repository]
---

# Source map

Use this page to jump from a repository file to the wiki concept that explains it. For behavior, prefer the concept pages over reading generated output artifacts. The [quickstart](quickstart.md) links all major pages, while this map is optimized for file navigation.

## Primary source files

| Path | Role | Wiki concept |
|---|---|---|
| `/job_boards.py` | Single-file CLI and runtime: HTTP fetch, per-ATS source adapters, board discovery, matching, board scan, CSV/JSON writing, SQLite persistence. | [Architecture overview](architecture/overview.md), [job scrape workflow](workflows/job-scrape.md), [board discovery workflow](workflows/board-discovery.md), [data model](architecture/data-model.md) |
| `/test_job_boards.py` | Dependency-free offline regression suite. | [Testing guide](testing.md) |
| `/README.md` | User-facing product documentation, measured completeness/freshness claims, operating guidance, and design rationale. | [Quickstart](quickstart.md), [operations runbook](operations/runbook.md) |
| `/boards.seed.json` | Small committed object of known-good board slugs keyed by `ashby`, `greenhouse`, and `lever` for fresh clones and discovery preservation. | [Board discovery workflow](workflows/board-discovery.md) |
| `/.gitignore` | Prevents generated scrape outputs and caches from being committed while allowing `boards.seed.json`. | [Operations runbook](operations/runbook.md), [data model](architecture/data-model.md) |
| `/LICENSE` | MIT license. | [Quickstart](quickstart.md) |

## Generated and local artifacts

| Path pattern | Meaning | Handling |
|---|---|---|
| `/boards.json` | Generated per-platform board cache after refreshes. | Treat as local operational output and avoid committing. |
| `/*.csv` | Generated query snapshots. | Ignored by git; useful to users, not source evidence for code behavior. |
| `/*.json` except `/boards.seed.json` | Generated query snapshots or caches. | Ignored by git. |
| `/*.db` | SQLite scrape history. | Ignored by git; lifecycle semantics are documented in [data model](architecture/data-model.md). |
| `/__pycache__/` | Python bytecode cache. | Ignored. |

The working tree observed during earlier wiki generation included generated output files such as CSV, JSON, database, and board-cache snapshots. They demonstrate expected artifact types but should not be treated as canonical source.

## Automation and agent files

| Path | Role | Notes |
|---|---|---|
| `/.github/workflows/openwiki-drift-check.yml` | PR guard that fails when tracked source/docs change without an `openwiki/` update. | Does not regenerate docs or need model credentials; see [operations](operations/runbook.md). |
| `/.github/workflows/openwiki-update.yml` | Manual OpenWiki update workflow, with the generated daily schedule commented out until `OPENROUTER_API_KEY` exists. | Uses pinned actions and sets up `uv`; see [operations](operations/runbook.md). |
| `/AGENTS.md` | Agent-facing pointer to OpenWiki docs. | Do not rewrite during normal wiki updates. |
| `/CLAUDE.md` | Claude-facing pointer to OpenWiki docs. | Do not rewrite during normal wiki updates. |
| `/openwiki/INSTRUCTIONS.md` | User-authored OpenWiki brief for this repository. | Control metadata, not generated documentation. |
| `/openwiki/quickstart.md` and linked pages | Generated code wiki. | Update through OpenWiki runs and keep links/source references accurate. |

## Function-level landmarks in `/job_boards.py`

| Function or symbol | Why it matters |
|---|---|
| `SOURCES`, `COLLINFO`, `WAYBACK_CDX` | External integration points and per-ATS API/domain configuration. |
| `fetch()` | Shared HTTP behavior, retries, gzip, status-to-exception mapping, and user agent. |
| `normalize_ashby()`, `normalize_greenhouse()`, `normalize_lever()` | Platform payload adapters into the shared row shape. |
| `board_url()` | Builds platform posting API URLs and adds Greenhouse `content=true` only when grep needs descriptions. |
| `slug_from_url()`, `_add()`, `plausible()` | Archive URL to slug candidate pipeline. |
| `candidates_from_wayback()`, `candidates_from_commoncrawl()` | Discovery integrations. |
| `board_exists()`, `discover_boards()`, `load_boards()` | Board validation, union-with-known behavior, and cache loading. |
| `matches()`, `plain_text()`, `fragments()`, `scan_board()` | Filtering and row construction. |
| `_create_table()`, `_prepare()`, `save()` | SQLite persistence, migration, and lifecycle rules. |
| `main()` | CLI contract and orchestration. |

## Git-history landmarks

Recent history is especially useful when reviewing changes:

- Discovery moved from a Common Crawl-centered approach to Wayback-first plus `HEAD` validation.
- Board cache handling changed so generated discovery output is local while `boards.seed.json` remains committed.
- Description search was added through `--grep` with fragment retention rather than whole-description storage.
- SQLite persistence grew from upsert history into disappearance tracking with `closed_at`.
- The scraper generalized from Ashby-only to Ashby, Greenhouse, and Lever through source adapters, `--ats`, shared `FIELDS`, and a composite `(ats, id)` key.
- OpenWiki PR automation changed from an attempted regenerating sync workflow to a drift-check workflow that only enforces a committed wiki update.

These history landmarks explain cross-page relationships: [board discovery](workflows/board-discovery.md) supplies coverage, [job scraping](workflows/job-scrape.md) emits normalized filtered rows, and the [data model](architecture/data-model.md) decides when missing rows can become closed postings.
