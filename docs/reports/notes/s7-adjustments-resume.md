# s7-adjustments resume (F2) - state: DONE (2026-11-13)
All fixes of the 2nd financial review (PR #15 comment 6834401619) are implemented and verified: B1, S1, M1-M4; mypy FixtureClaims fixed.
Commits: 0a3fb4f (WIP, now verified), 71279fc (tests, M4 EOL restore), 64990c5 (merge of main, brings test_end_to_end.py), f2c3649 (mypy fix).
Verification (REBATE_TEST_DB=rebate_test_f2, one-off api container):
- full backend `pytest -q --no-cov`: 1897 passed, 4 deselected, 0 failed.
- isolation `pytest tests/isolation tests/db/test_schema_lint.py tests/db/test_migration_helpers.py`: 360 passed; `python -m tests.isolation.negative_control`: OK (181 RLS-dependent tests detected the breach).
- ruff check, ruff format --check, mypy, lint-imports (13 contracts kept): clean.
- No new table/route (migration only patches two trigger functions), so the isolation suite needed no new rows.
Left: financial 2nd-pass on the fix diff, then merge into s7-claims -> PR #15.
