# Files

- [Job Boards Output and Persistence Model](data-model.md) - Documents the scraper's cross-ATS CSV and JSON row shape, SQLite jobs table, composite upsert rules, and posting lifecycle semantics for first_seen, last_seen, and closed_at.
- [Job Boards Runtime Architecture](overview.md) - Explains the two-phase architecture of the public job-board scraper, including multi-ATS board discovery, adapter-based job scanning, output generation, and SQLite persistence.
