# L1 notes: S11.6 drill and S11.7 guides (2026-11-13)

Choices made unattended:
- Drill used the existing scripts unchanged; `ENV_FILE=.env` and `COMPOSE` pointed at the dev stack. No target-override change was needed (restore.sh already takes a fresh DB and bucket name).
- Backup kept in gitignored `backups/` inside the worktree (not committed).
- Isolation/golden suite ran against a fresh `rebate_test_l1`, not the restored DB (the suite creates its own DB). Stated as a caveat in the report.
- Dropped `rebate_drill`, `rebate_test_l1` and bucket `rebate-files-drill` (all my own).
- Guides: S7-S10 marked "not in Release 1 yet"; audit hash-chain, PITR, pen test, DPA, nosniff (K-10) listed as not done in the security overview.
- Doc deltas for the orchestrator: IMPLEMENTATION_PLAN S11.6/S11.7 boxes NOT ticked (S11.6 lacks WAL/PITR and versioning proof; tick at owner discretion). `backup-restore.md` could mention the COMPOSE/ENV_FILE override for non-production stacks.
- Edited `docs/PROJECT_STATUS.md` (one risk line R-2) as the task asked.
