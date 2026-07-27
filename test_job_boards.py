#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Offline self-check. Run: uv run test_job_boards.py  (or python3 test_job_boards.py)"""

import csv
import io
import json
import re
import sqlite3
import tempfile
from pathlib import Path

from job_boards import (
    FIELDS,
    board_url,
    fragments,
    matches,
    normalize_ashby,
    normalize_greenhouse,
    normalize_lever,
    plain_text,
    plausible,
    save,
    scan_board,
    slug_from_url,
)

BASE = {f: "" for f in FIELDS}


def _with_fetch(payload, fn):
    """Run fn with job_boards.fetch stubbed to return payload."""
    import job_boards
    original = job_boards.fetch
    job_boards.fetch = (
        payload if callable(payload) else (lambda *a, **k: json.dumps(payload).encode())
    )
    try:
        return fn()
    finally:
        job_boards.fetch = original


# --------------------------------------------------------------------------- #
# Discovery
# --------------------------------------------------------------------------- #


def test_slug_parsing():
    assert slug_from_url("https://jobs.ashbyhq.com/0g/4fc6ba8a-1111?ref=x") == "0g"
    assert (
        slug_from_url("https://jobs.ashbyhq.com/A1%20Garage%20Door%20Service/x")
        == "A1 Garage Door Service"
    )
    assert slug_from_url("https://jobs.ashbyhq.com/") is None


def test_cdx_jsonl_parsing():
    """Common Crawl returns JSONL, not a JSON array, and mixes in junk paths."""
    payload = "\n".join([
        '{"url": "https://jobs.ashbyhq.com/ramp/abc-123?ref=x"}',
        '{"url": "https://jobs.ashbyhq.com/Ramp/def-456"}',        # dupe, other casing
        '{"url": "https://jobs.ashbyhq.com/A1%20Garage%20Door/x"}',  # spaces in slug
        "",                                                         # blank line
        '{"url": "https://jobs.ashbyhq.com/_next/static/z.js"}',    # junk, 404s later
        '{"url": "https://jobs.ashbyhq.com/"}',                     # no slug at all
    ])
    seen = {}
    for line in payload.splitlines():
        if not line.strip():
            continue
        slug = slug_from_url(json.loads(line)["url"])
        if slug:
            seen.setdefault(slug.lower(), slug)
    assert sorted(seen.values()) == ["A1 Garage Door", "_next", "ramp"]
    assert seen["ramp"] == "ramp"  # first-seen casing wins over "Ramp"


def test_plausible_rejects_archive_noise_but_keeps_real_slugs():
    """The archives yield millions of URLs; the shape filter makes validation cheap."""
    for good in ("ramp", "keeling-labs", "A1 Garage Door Service", "ScribdInc", "0g"):
        assert plausible(good), good
    for junk in (
        "_next", "api", "favicon.ico", "robots.txt",          # site plumbing
        "root.6511f3ee_758c_4ed4_8fce_254a11715ed7",          # Ashby embed path
        "4fc6ba8a-532f-46a7-b1f3-d5490d78120e",               # a posting id, not a board
        "$10.2K", '"80e0bf43", "environment":"production"',   # comp strings, JS blobs
        "블록웍스", "", "-" * 80,
    ):
        assert not plausible(junk), junk


def test_root_prefix_is_only_junk_for_ashby():
    """`root.<uuid>` is an Ashby embed path; it carries no meaning elsewhere."""
    assert not plausible("root.abc", "ashby")
    assert plausible("root.abc", "greenhouse")


def test_board_exists_uses_head_and_maps_404_to_false():
    """Validation must not download board payloads — thousands of GETs would be GBs."""
    import job_boards

    calls = []

    def fake_fetch(url, timeout=30, retries=4, method="GET"):
        calls.append(method)
        if "nope" in url:
            raise job_boards.NotFound(url)
        return b""

    original = job_boards.fetch
    job_boards.fetch = fake_fetch
    try:
        assert job_boards.board_exists("ashby", "ramp") is True
        assert job_boards.board_exists("greenhouse", "nope12345") is False
    finally:
        job_boards.fetch = original
    assert calls == ["HEAD", "HEAD"], f"expected HEAD probes, got {calls}"


# --------------------------------------------------------------------------- #
# Per-ATS normalisation
# --------------------------------------------------------------------------- #


def test_normalize_ashby():
    row = normalize_ashby({
        "id": "2b401986", "title": "Senior Software Engineer, Data",
        "department": "Builder", "team": "Data", "employmentType": "FullTime",
        "location": "Remote - US", "isRemote": True, "workplaceType": "Remote",
        "publishedAt": "2026-06-30T19:02:11.162+00:00",
        "jobUrl": "https://jobs.ashbyhq.com/abridge/2b401986",
        "isListed": True, "descriptionPlain": "we use Rust",
    })
    assert row["title"] == "Senior Software Engineer, Data"
    assert row["isRemote"] is True
    assert row["publishedAt"].startswith("2026-06-30")
    assert row["_description"] == "we use Rust"
    # unlisted postings are dropped by the normaliser, not downstream
    assert normalize_ashby({"id": "x", "title": "Secret", "isListed": False}) is None


def test_normalize_greenhouse():
    """location is a nested object and there is no remote flag."""
    row = normalize_greenhouse({
        "id": 7954688, "title": "Account Executive",
        "location": {"name": "San Francisco, CA"},
        "absolute_url": "https://stripe.com/jobs/search?gh_jid=7954688",
        "first_published": "2026-06-02T08:58:57-04:00",
        "updated_at": "2026-07-27T11:17:30-04:00",
    })
    assert row["id"] == "7954688", "integer ids must become strings for the TEXT key"
    assert row["location"] == "San Francisco, CA"
    assert row["jobUrl"].endswith("gh_jid=7954688")
    assert row["publishedAt"] == "2026-06-02T08:58:57-04:00"
    assert row["isRemote"] is False
    assert normalize_greenhouse(
        {"id": 1, "title": "T", "location": {"name": "Remote - US"}}
    )["isRemote"] is True


def test_normalize_lever():
    """Two traps: the title is `text`, and createdAt is epoch milliseconds."""
    row = normalize_lever({
        "id": "f25a6c49", "text": "Compounding Pharmacy Technician",
        "categories": {"commitment": "Full-time", "location": "Romeoville, IL",
                       "team": "Pharmacy"},
        "createdAt": 1750119882479,
        "workplaceType": "onsite",
        "hostedUrl": "https://jobs.lever.co/ro/f25a6c49",
        "descriptionPlain": "compounding meds",
    })
    assert row["title"] == "Compounding Pharmacy Technician", "Lever's title key is `text`"
    assert row["employmentType"] == "Full-time"
    assert row["team"] == "Pharmacy"
    assert row["isRemote"] is False
    # epoch ms -> ISO, or it sorts wrongly against the other platforms
    assert row["publishedAt"].startswith("2025-06-17T"), row["publishedAt"]
    assert "T" in row["publishedAt"] and row["publishedAt"].endswith("+00:00")


def test_normalizers_survive_explicit_nulls():
    """Every ATS sends JSON null for fields it has no value for.

    Regression: Greenhouse sends {"location": {"name": null}}. `loc.get("name", "")`
    returns None there — the key exists, so the default never applies — and the
    subsequent .lower() raised, aborting the whole board. Seven Greenhouse boards
    were dropped entirely, xai's 220 jobs among them.
    """
    gh = normalize_greenhouse({"id": 1, "title": None, "location": {"name": None},
                               "absolute_url": None, "first_published": None})
    assert gh["location"] == "" and gh["isRemote"] is False and gh["title"] == ""

    ash = normalize_ashby({"id": "1", "isListed": True, "title": None,
                           "location": None, "descriptionPlain": None})
    assert ash["title"] == "" and ash["location"] == ""

    lev = normalize_lever({"id": "1", "text": None, "categories": None,
                           "createdAt": None, "workplaceType": None})
    assert lev["title"] == "" and lev["publishedAt"] == "" and lev["team"] == ""


def test_every_normalizer_fills_the_same_keys():
    """A missing key in one adapter becomes a silently empty column."""
    samples = [
        (normalize_ashby, {"id": "1", "isListed": True}),
        (normalize_greenhouse, {"id": 1}),
        (normalize_lever, {"id": "1"}),
    ]
    expected = {f for f in FIELDS if f not in ("ats", "company", "matched")}
    for fn, payload in samples:
        keys = set(fn(payload)) - {"_description"}
        assert keys == expected, f"{fn.__name__} produced {keys ^ expected}"


def test_greenhouse_content_param_only_when_grepping():
    """Descriptions cost ~26x the bytes, so they must be opt-in."""
    assert "content=true" not in board_url("greenhouse", "stripe")
    assert "content=true" in board_url("greenhouse", "stripe", want_content=True)
    # platforms that always return descriptions must not gain a stray parameter
    assert "content=true" not in board_url("ashby", "ramp", want_content=True)
    assert "mode=json" in board_url("lever", "ro", want_content=True)


# --------------------------------------------------------------------------- #
# Filtering
# --------------------------------------------------------------------------- #


def test_fuzzy_matching():
    assert matches("Senior Software Engineer, Backend", "software engineer")
    assert matches("SOFTWARE ENGINEER II", "software engineer")
    assert matches("Software Engineer", "senior software engineer, backend")
    assert not matches("Engineering Manager", "software engineer")


def test_fuzzy_does_not_match_generic_one_word_titles():
    """The reverse direction must not drag in every one-word title in the query."""
    for junk in ("Engineer", "Software", "Senior"):
        assert not matches(junk, "senior software engineer"), junk
    assert matches("Software Engineer", "senior software engineer")


def test_empty_query_matches_nothing():
    assert not matches("Chef", "")
    assert not matches("", "software engineer")


def test_exact_matching():
    assert matches("Software Engineer", "software engineer", "exact")
    assert matches("  SOFTWARE ENGINEER  ", "software engineer", "exact")
    assert not matches("Senior Software Engineer, Backend", "software engineer", "exact")
    assert not matches("Software Engineer", "senior software engineer", "exact")


def test_plain_text_strips_markup_and_entities():
    assert plain_text("<p>Rust &amp; Go</p>\n\n  <b>here</b>") == "Rust & Go here"
    assert plain_text("") == ""


def test_fragments_give_context_and_dedupe():
    text = "we use kubernetes daily. " * 3 + "the end"
    hits = fragments(text, re.compile("kubernetes", re.IGNORECASE))
    assert len(hits) <= 2
    assert all("kubernetes" in h for h in hits)
    assert len(set(hits)) == len(hits)
    assert fragments("nothing here", re.compile("kubernetes")) == []


def test_scan_board_filters_and_flattens():
    """isListed/remote filtering, and that descriptions never reach the output."""
    board = {"jobs": [
        {"id": "1", "title": "Software Engineer", "isListed": True, "isRemote": True,
         "location": "Remote", "descriptionHtml": "<p>huge</p>",
         "descriptionPlain": "huge", "jobUrl": "u1"},
        {"id": "2", "title": "Software Engineer", "isListed": False, "isRemote": True},
        {"id": "3", "title": "Chef", "isListed": True, "isRemote": True},
        {"id": "4", "title": "Software Engineer II", "isListed": True, "isRemote": False},
    ]}
    rows = _with_fetch(board, lambda: scan_board("ashby", "acme", "software engineer", False, "fuzzy"))
    remote = _with_fetch(board, lambda: scan_board("ashby", "acme", "software engineer", True, "fuzzy"))

    assert [r["title"] for r in rows] == ["Software Engineer", "Software Engineer II"]
    assert [r["title"] for r in remote] == ["Software Engineer"]
    assert rows[0]["company"] == "acme" and rows[0]["ats"] == "ashby"
    assert set(rows[0]) == set(FIELDS), "row must be exactly the declared columns"
    assert not any("escription" in k for k in rows[0]), "descriptions must be dropped"


def test_scan_board_handles_levers_bare_list_payload():
    """Lever's payload is the list itself, not {'jobs': [...]}."""
    payload = [{"id": "a", "text": "Software Engineer", "categories": {},
                "createdAt": 1750119882479, "hostedUrl": "u"}]
    rows = _with_fetch(payload, lambda: scan_board("lever", "ro", "software engineer", False, "fuzzy"))
    assert len(rows) == 1
    assert rows[0]["ats"] == "lever" and rows[0]["title"] == "Software Engineer"


def test_grep_filters_on_description_and_records_context():
    board = {"jobs": [
        {"id": "1", "title": "Backend Engineer", "isListed": True,
         "descriptionPlain": "You will write <b>Rust</b> and Go all day."},
        {"id": "2", "title": "Backend Engineer", "isListed": True,
         "descriptionPlain": "We deeply value trust and integrity."},
    ]}
    loose = _with_fetch(board, lambda: scan_board(
        "ashby", "acme", None, False, "fuzzy", re.compile("rust", re.I)))
    strict = _with_fetch(board, lambda: scan_board(
        "ashby", "acme", None, False, "fuzzy", re.compile(r"\brust\b", re.I)))

    assert len(loose) == 2, "unbounded 'rust' also matches 'trust' — the documented footgun"
    assert len(strict) == 1, "word-bounded regex excludes 'trust'"
    assert "Rust" in strict[0]["matched"]
    assert "<b>" not in strict[0]["matched"], "markup must be stripped from fragments"


def test_invalid_payload_raises_rather_than_returning_nothing():
    raised = False
    try:
        _with_fetch({"error": "nope"}, lambda: scan_board("ashby", "acme", "engineer", False, "fuzzy"))
    except ValueError:
        raised = True
    assert raised, "a shape change must fail loudly, not read as 'no jobs found'"


def test_no_filters_returns_every_listed_job():
    board = {"jobs": [
        {"id": "1", "title": "Chef", "isListed": True},
        {"id": "2", "title": "Welder", "isListed": True},
        {"id": "3", "title": "Secret Role", "isListed": False},
    ]}
    rows = _with_fetch(board, lambda: scan_board("ashby", "acme", None, False, "fuzzy", None))
    assert [r["title"] for r in rows] == ["Chef", "Welder"]


# --------------------------------------------------------------------------- #
# Storage
# --------------------------------------------------------------------------- #


def test_db_upsert_preserves_history():
    """first_seen must survive re-scrapes; that is the point of the database."""
    row = BASE | {"ats": "ashby", "id": "job-1", "company": "acme", "title": "SWE"}
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "t.db"
        new, updated, _ = save([row], db, "2026-01-01T00:00:00+00:00")
        assert (new, updated) == (1, 0)
        new, updated, _ = save([row | {"title": "Senior SWE"}], db, "2026-02-02T00:00:00+00:00")
        assert (new, updated) == (0, 1)
        got = sqlite3.connect(db).execute(
            "SELECT title, first_seen, last_seen FROM jobs").fetchall()
        assert got == [("Senior SWE", "2026-01-01T00:00:00+00:00", "2026-02-02T00:00:00+00:00")]


def test_same_id_on_two_platforms_is_two_rows():
    """Greenhouse ids are integers; a bare id key would collide across platforms."""
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "t.db"
        save([
            BASE | {"ats": "ashby", "id": "123", "company": "acme", "title": "A"},
            BASE | {"ats": "greenhouse", "id": "123", "company": "other", "title": "G"},
        ], db, "2026-01-01T00:00:00+00:00")
        got = dict(sqlite3.connect(db).execute("SELECT ats, title FROM jobs"))
        assert got == {"ashby": "A", "greenhouse": "G"}


def test_db_keeps_grep_context_from_earlier_runs():
    row = BASE | {"ats": "ashby", "id": "job-1", "company": "acme"}
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "t.db"
        save([row | {"matched": "uses Rust daily"}], db, "2026-01-01T00:00:00+00:00")
        save([row], db, "2026-02-02T00:00:00+00:00")  # no --grep this time
        (kept,) = sqlite3.connect(db).execute("SELECT matched FROM jobs").fetchone()
        assert kept == "uses Rust daily"


def test_migrates_a_single_ats_database():
    """Upgrade path: an id-keyed, ats-less database from before Greenhouse existed.

    Fresh-schema tests never touch this branch, which is exactly how the previous
    migration shipped broken.
    """
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "old.db"
        legacy = [f for f in FIELDS if f not in ("ats", "id")]
        con = sqlite3.connect(db)
        con.executescript(f"""
            CREATE TABLE jobs (id TEXT PRIMARY KEY,
                               {','.join(f'{c} TEXT' for c in legacy)},
                               first_seen TEXT NOT NULL, last_seen TEXT NOT NULL,
                               closed_at TEXT);
            CREATE INDEX jobs_company ON jobs(company);
        """)
        con.execute(
            "INSERT INTO jobs (id, company, title, first_seen, last_seen) VALUES "
            "('old-1','acme','Old Role','2026-01-01T00:00:00+00:00',"
            "'2026-01-01T00:00:00+00:00')"
        )
        con.commit()
        con.close()

        row = BASE | {"ats": "greenhouse", "id": "new-1", "company": "gh-co"}
        save([row], db, "2026-02-02T00:00:00+00:00")

        con = sqlite3.connect(db)
        cols = {c[1] for c in con.execute("PRAGMA table_info(jobs)")}
        assert "ats" in cols and "closed_at" in cols
        got = dict(con.execute("SELECT id, ats FROM jobs"))
        assert got == {"old-1": "ashby", "new-1": "greenhouse"}, \
            "existing rows must be labelled ashby, not dropped"
        # history survives the table rebuild
        (fs, title) = con.execute(
            "SELECT first_seen, title FROM jobs WHERE id='old-1'").fetchone()
        assert fs == "2026-01-01T00:00:00+00:00" and title == "Old Role"
        assert (("ats", "id") == tuple(
            c[1] for c in con.execute("PRAGMA table_info(jobs)") if c[5]
        )), "primary key must be (ats, id)"


def test_migrates_a_database_created_before_closed_at():
    """The older upgrade path still works, now via the rebuild."""
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "old.db"
        legacy = [f for f in FIELDS if f not in ("ats", "id")]
        con = sqlite3.connect(db)
        con.executescript(f"""
            CREATE TABLE jobs (id TEXT PRIMARY KEY,
                               {','.join(f'{c} TEXT' for c in legacy)},
                               first_seen TEXT NOT NULL, last_seen TEXT NOT NULL);
        """)
        con.execute(
            "INSERT INTO jobs (id, company, first_seen, last_seen) VALUES "
            "('old-1','acme','2026-01-01T00:00:00+00:00','2026-01-01T00:00:00+00:00')"
        )
        con.commit()
        con.close()

        row = BASE | {"ats": "ashby", "id": "new-1", "company": "acme"}
        new, _, closed = save([row], db, "2026-02-02T00:00:00+00:00",
                              covered=[("ashby", "acme")])
        con = sqlite3.connect(db)
        assert "closed_at" in {c[1] for c in con.execute("PRAGMA table_info(jobs)")}
        got = dict(con.execute("SELECT id, closed_at FROM jobs"))
        assert got["old-1"] == "2026-02-02T00:00:00+00:00"
        assert got["new-1"] is None
        assert (new, closed) == (1, 1)
        idx = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='index'")}
        assert "jobs_closed_at" in idx


def test_unfiltered_run_closes_vanished_postings():
    a = BASE | {"ats": "ashby", "id": "a", "company": "acme"}
    b = BASE | {"ats": "ashby", "id": "b", "company": "acme"}
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "t.db"
        save([a, b], db, "2026-01-01T00:00:00+00:00", covered=[("ashby", "acme")])
        _, _, closed = save([a], db, "2026-02-02T00:00:00+00:00",
                            covered=[("ashby", "acme")])
        assert closed == 1
        got = dict(sqlite3.connect(db).execute("SELECT id, closed_at FROM jobs"))
        assert got["a"] is None and got["b"] == "2026-02-02T00:00:00+00:00"

        _, _, closed = save([a, b], db, "2026-03-03T00:00:00+00:00",
                            covered=[("ashby", "acme")])
        assert closed == 0
        got = dict(sqlite3.connect(db).execute("SELECT id, closed_at FROM jobs"))
        assert got["b"] is None, "a reappearing posting must reopen"


def test_filtered_run_never_closes_anything():
    """A --title run missing a job is ambiguous: gone, or just not matched."""
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "t.db"
        save([BASE | {"ats": "ashby", "id": "a", "company": "acme"},
              BASE | {"ats": "ashby", "id": "b", "company": "acme"}],
             db, "2026-01-01T00:00:00+00:00", covered=[("ashby", "acme")])
        _, _, closed = save([BASE | {"ats": "ashby", "id": "a", "company": "acme"}],
                            db, "2026-02-02T00:00:00+00:00", covered=None)
        assert closed == 0
        (open_rows,) = sqlite3.connect(db).execute(
            "SELECT COUNT(*) FROM jobs WHERE closed_at IS NULL").fetchone()
        assert open_rows == 2


def test_closing_is_scoped_to_boards_actually_scanned():
    """A --limit or --ats run must not close postings it never visited."""
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "t.db"
        save([BASE | {"ats": "ashby", "id": "a", "company": "acme"},
              BASE | {"ats": "greenhouse", "id": "z", "company": "acme"}],
             db, "2026-01-01T00:00:00+00:00",
             covered=[("ashby", "acme"), ("greenhouse", "acme")])
        # this run covered only ashby/acme, and saw nothing there
        _, _, closed = save([], db, "2026-02-02T00:00:00+00:00",
                            covered=[("ashby", "acme")])
        assert closed == 1
        got = dict(sqlite3.connect(db).execute("SELECT ats, closed_at FROM jobs"))
        assert got["ashby"] is not None
        assert got["greenhouse"] is None, \
            "same company name on another platform must be left alone"


def test_db_skips_rows_without_an_id():
    with tempfile.TemporaryDirectory() as tmp:
        assert save([BASE | {"ats": "ashby"}], Path(tmp) / "t.db",
                    "2026-01-01T00:00:00+00:00") == (0, 0, 0)


# --------------------------------------------------------------------------- #
# Output
# --------------------------------------------------------------------------- #


def test_user_agent_is_header_safe():
    """http.client encodes headers as latin-1; non-ASCII breaks every request."""
    import job_boards
    job_boards.UA.encode("latin-1")
    assert job_boards.UA.isascii()


def test_csv_quoting():
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=FIELDS)
    w.writeheader()
    w.writerow(BASE | {
        "ats": "ashby", "company": "acme",
        "title": 'Senior Software Engineer, Backend "Core"',
        "location": "Remote – US • EU",
    })
    row = list(csv.DictReader(io.StringIO(buf.getvalue())))[0]
    assert row["title"] == 'Senior Software Engineer, Backend "Core"'
    assert row["location"] == "Remote – US • EU"


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
    print(f"ok ({len(tests)} tests)")
