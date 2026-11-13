# Finish plan (2026-11-13 evening; deadline 2026-11-15)

Owner is short on tokens. Claude (orchestrator) plans, reviews, fixes, gates. External agents build UI.
Fixed setup: **2 Claude agents** at a time — 1 sonnet builder (big backend tasks), 1 opus reviewer (reviews
everything: the builder, OpenCode, Antigravity). Orchestrator runs every gate and every Playwright run itself
(neither external agent can reach localhost).

## Who does what
| Track | Who | Work |
|---|---|---|
| UI S7 adjustments | Antigravity — DONE (fef83b3, committed by orchestrator) | reviewer → orchestrator Playwright → merge after #15 |
| UI S8 workbench | Antigravity — DONE (063e0c4, committed by orchestrator) | review → Playwright → merge after S8 |
| UI S9 recovery | OpenCode — DONE (9f8a39d) | review → Playwright → merge after S9 |
| UI S10 dashboard | external agent — DONE (0084ee0, committed by orchestrator) | review → Playwright → merge after S10 |
| Backend chain | Claude builder B1 | S9 ← final S8; S10 ← S9; S10's 3 failing tests green |
| Reviews | Claude reviewer | queue below, one at a time |
| Gates, Playwright, merges prep, fixes, docs | orchestrator | ci-local 6/6 per PR; owner clicks merge |

## Reviewer queue (in order)
1. ui/s7-adjustments — done: NOT MERGEABLE → 15 findings fixed (dccc040); Playwright pending
2. S9 on final S8 + D-N2 — done: MERGEABLE WITH MINORS → fixed (1983f5a)
3. S10 (7054beb; re-run test_reporting*.py + heads first) — financial + security + DB (first full S10 review on the owner money rule)
4. ui/s8 when Antigravity reports; ui/s9 when OpenCode reports; ui/s10 when built; ui/s7 + ui/qa-1 leftovers

## Merge order (one at a time; each: merge latest main → ci-local 6/6 posted on the PR → owner merges)
#21 S11.1 → #15 S7 (+ ui/s7, ui/s7-adjustments) → S8 (+ ui/s8) → S9 (+ ui/s9) → S10 (+ ui/s10) → #22 S11.3 → ui/qa-1.
UI branches merge right after their backend, after main is merged into them and their Playwright specs pass.
Never push to main while a gate or a pending merge depends on main staying put.

## After the chain (builder's next big tasks)
- S11.4 a11y pass on all screens (axe in every spec; fixes are UI-only).
- S11.2 perf only if the owner raises Docker memory (D-N9).
- RELEASE_1_REPORT.md (orchestrator) once S7–S10 + UI are on main.
- Owner-only: S11.5 deploy/hosting, S11.8 pilot review, D-N4, D-N8, D-N11.

## Token rules
Short prompts that point to files; reviewers read diffs only; no full-suite runs outside the gate; one fix round,
then the orchestrator verifies small minors itself instead of another review pass.
