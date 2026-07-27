<!-- OPENWIKI:START -->

## OpenWiki

This repository uses OpenWiki for recurring code documentation. Start with `openwiki/quickstart.md`, then follow its links to architecture, workflows, domain concepts, operations, integrations, testing guidance, and source maps.

The scheduled OpenWiki GitHub Actions workflow refreshes the repository wiki. Do not hand-edit generated OpenWiki pages unless explicitly asked; prefer updating source code/docs and letting OpenWiki regenerate.

<!-- OPENWIKI:END -->

<!-- Hand-written; outside the OPENWIKI markers so regeneration preserves it. -->

## Running the tests

The suite is offline, needs no network and no dependencies, and finishes in about a
second. Run it before and after any change — do not document behaviour as unverified
without trying both commands below.

```bash
uv run test_job_boards.py     # preferred: uv provisions Python 3.11
python3 test_job_boards.py    # fallback: works on any Python >= 3.9, including
                              # macOS's system python3, with uv absent from PATH
```

The fallback exists specifically for tooling that runs with a minimal `PATH`. If `uv`
is not found, use the second command rather than reporting that the tests could not be
run. Both print `ok` on success.

## Do not run casually

`--refresh-boards` and `--all` are live network operations across ~12,000 boards on
three platforms. Never in a loop. Everything you need to verify a code change is
covered by the offline suite. `--limit 10` plus `--ats <one>` is the cheap way to
exercise a real request path.

`--grep` on Greenhouse requests full job descriptions, which is roughly **26x** the
bytes of a normal run. Do not add it casually to an unlimited run.

Set `JOB_SCRAPER_CONTACT` to a real address before any network run. Ask for one;
do not invent it.

## Read before changing behaviour

`README.md` has a "For coding agents (LLMs)" section listing behaviours that look like
bugs and are deliberate, each pinned by a test. Read it before changing filter logic,
the database lifecycle, board validation, or any per-platform normaliser.

The normalisers in `job_boards.py` are where platform differences live. Two that have
already caused silent, wrong-looking-correct output: Lever's job title is `text`, not
`title`, and its `createdAt` is epoch milliseconds rather than ISO.
