# Project Overview

**Product name:** Veyra Recover · **Company:** Veyra AI. Use "Veyra Recover" in user-facing text (app title, emails, exports, login page). Internal code names (`rebate`, `rebate_engine`, `rebate_matching`, database/role names such as `rebate_app`) are unchanged to keep the documents and schema consistent; renaming them is optional and would be a deliberate change.

> **Status:** Draft for owner review · **Version:** 0.1 · **Date:** 2026-11-09
> **Read first:** this file, then `CLAUDE.md`. Everything else is referenced from here.

## 0. How to read these documents

| Label | Meaning |
|---|---|
| **APPROVED** | Explicitly decided by the owner, or delegated by the owner to the CTO role and recorded in `ARCHITECTURE_DECISIONS.md` as *Accepted*. Implement as written. |
| **PROPOSED** | A recommendation that significantly affects product, security, or business direction. Needs owner approval before it is relied on. Listed in §9 and in `PROJECT_STATUS.md`. |
| **ASSUMPTION** | A working assumption about the market or customers that has not been validated. Marked `A-xx` in §8. Never build irreversible things on top of one. |
| **SYNTHETIC** | Data that is fabricated for testing. Always labelled as such. |

## 1. Vision

A web platform that sits beside a wholesale distributor's ERP and acts as the **system of record for money that manufacturers owe the distributor** after the sale: special pricing agreement (SPA / ship-and-debit / billback) claims and volume rebates.

> **Promise:** Find the money the distributor is owed, prove it line by line, and help them collect it before the claim window closes.

The product is **not** an ERP, not a general pricing tool, and does not post to the customer's general ledger in Release 1.

## 2. Business problem

Distributors buy from manufacturers at standard cost and often sell to contractors at manufacturer-approved deviated prices. The manufacturer then owes the difference (SPA claim). Separately, manufacturers pay tiered, often retroactive, volume rebates. Today this runs in spreadsheets:

- Contracts arrive as PDFs and Excel files, each with different structure.
- Claims are assembled by hand, monthly, in each manufacturer's format.
- Manufacturers pay with one lump-sum credit memo, remittance, or payables deduction, and short-pay lines without a clean explanation.
- Short-pays are found late, or after the dispute window closes.
- Auditors ask for a trail from sale to cash and get "it's in a spreadsheet".

Three distinct problems (see the product assessment):

1. **Entitlement:** what is owed, per the agreement terms.
2. **Submission:** a correct claim to the manufacturer inside its window.
3. **Verification and recovery:** what was paid, why it was short, and whether it can still be contested. *This is the highest-value, least-served part and is the core of the product.*

All market figures (leakage %, hours saved, competitor prices, 52%/87% statistics) come from AI-generated research and are **unverified** (see A-01 to A-04).

## 3. Target customers (ASSUMPTION, see A-05 to A-08)

| Factor | Target |
|---|---|
| Type | US wholesale distributors buying from manufacturers and selling to contractors/businesses |
| Size | ~$20M–$150M revenue, 40–400 employees, 2–25 branches |
| Qualifier | **Annual claimable dollars** (SPA + rebates), not revenue. Rough floor: ~$1M/year, below which an $18K/year subscription is hard to justify |
| Verticals | One vertical first (see §6); architecture is vertical-agnostic |
| ERPs | Any. Release 1 works from file exports; no ERP connection |

## 4. Core product functionality (Release 1)

1. **Workspace and security:** tenants, users, roles, MFA, audit log, strict tenant isolation.
2. **Data import:** sales documents (invoices, credit notes/returns), purchase documents, product costs, customers, products. Saved column mappings, de-duplication, error reports, full lineage.
3. **Agreement management (manual + AI, one pipeline):** create agreements by hand, by spreadsheet template, or by uploading a PDF/Excel for AI extraction. All three produce an **agreement draft** in the same structure, run the same validation, and require human approval before activation. Versions are immutable.
4. **Deterministic claims engine:** pure calculation, full trace per line, reproducible.
5. **Missed-claims scan:** eligible-but-unclaimed lines. This is also the sales instrument.
6. **Claim batches:** grouped, exportable, frozen on submission; later corrections are explicit adjustments.
7. **Remittance reconciliation:** upload credit memos/remittances/deduction notices (CSV/Excel/PDF), match to claim lines, classify every variance.
8. **Recovery queue:** variance cases with deadlines, owners, notes, and outcomes.
9. **Dashboard and reports:** claimed / collected / short-paid / recovered / at-risk; rebate tier tracker; audit trail view.

Deferred to Release 2 (not approved for implementation): manufacturer-specific claim file builders, tier-crossing simulation, escalation chains, accounting export, ERP connectors, EDI/portal submission, SSO, forecasting, benchmarks. See `DEVELOPMENT_ROADMAP.md`.

## 5. User roles

| Role | Who | Can do |
|---|---|---|
| `admin` | Workspace owner/IT/finance lead | Everything, incl. users, settings, and approvals |
| `approver` | Controller, finance manager | Approve agreements, claim batch submission, write-offs, adjustments |
| `analyst` | Rebate/pricing analyst, senior accountant (daily user) | Import, draft agreements, run calculations, work cases, reconcile |
| `viewer` | CFO, auditors, executives | Read-only access to everything except settings and user admin |

Platform-level staff (`platform_support`) are not tenant members. They have no standing access; see `SECURITY.md` §support access.

## 6. First vertical (PROPOSED, ADR-019)

Recommendation: **electrical distribution** first, HVAC second. Reasoning, comparison, and the switching cost are in `ARCHITECTURE_DECISIONS.md` ADR-019. The architecture, schema, and engine contain **no vertical-specific code**. Vertical differences live in data: agreement rules, manufacturer settings, claim export layouts, reason-code mappings, and test fixtures. **This is a business-direction decision and awaits owner approval.**

## 7. Product goals

| Goal | Measure |
|---|---|
| G1 Correctness | Every claim amount reproducible from stored inputs; 100% of engine rule types covered by golden tests |
| G2 Traceability | Every displayed amount drills down to source file → row → agreement version → rule → claim → payment |
| G3 Value proof | Missed-claims scan produces a credible dollar figure from a customer's own data within one working session once data is loaded |
| G4 Recovery | No variance case misses a deadline unnoticed |
| G5 Safety | AI output never changes a financial record without a recorded human approval |
| G6 Low onboarding cost | Onboarding effort tracked per customer; saved mappings and manufacturer settings are reusable |

## 8. Assumptions requiring validation

| ID | Assumption | Validate by |
|---|---|---|
| A-01 | Distributors lose 1.5–4% of rebate/claim volume to leakage | Missed-claims scan on a real customer; customer interviews |
| A-02 | Analysts spend 40–80 hours/month on claims | Interviews |
| A-03 | Buyers will pay ~$7,500 setup + ~$1,500/month | Pricing conversations |
| A-04 | Competitor prices cited in the research PDF are accurate | Vendor quotes, G2, buyer interviews |
| A-05 | Target size band has enough claimable dollars | Qualify on claimable dollars per prospect |
| A-06 | Customers will share sample data for a scan | Pilot conversations |
| A-07 | Most customers can export the required fields from their ERP | First pilot |
| A-08 | Single currency (USD) per tenant is sufficient for US customers | Interviews |
| A-09 | Manufacturer claim windows/dispute windows are known and configurable per manufacturer | Customer onboarding |
| A-10 | Some manufacturers pay by payables deduction rather than credit memo | Interviews |
| A-11 | SPA reference numbers are often missing from invoice lines | Customer exports |
| A-12 | Synthetic documents are structurally representative of real ones | Replace with anonymised real documents as soon as available |

## 9. Open decisions (need owner approval)

| ID | Decision | Where |
|---|---|---|
| OD-1 | First vertical: electrical (recommended) vs HVAC | ADR-019 |
| OD-2 | Default LLM provider and whether customer documents may be sent to it (data-processing terms, per-tenant opt-out) | ADR-012 |
| OD-3 | Production Postgres hosting: self-run vs managed standard Postgres (local dev is self-run Docker either way) | ADR-023 |
| OD-4 | Production hosting region/provider (US region assumed) | ADR-023 |
| OD-5 | Release 2 scope and ordering, after pilot feedback | `DEVELOPMENT_ROADMAP.md` |

## 10. MVP boundaries vs. long-term vision

**Release 1 (production-ready core)** = Stages S0–S11 in `DEVELOPMENT_ROADMAP.md`. It is built to production standards (security, audit, migrations, tests), not as a demo. "MVP" here limits **breadth** (no ERP connectors, no EDI, no accounting posting), never **quality**.

**Long-term:** ERP connectors starting with one platform, EDI 844/849 and portals, accounting export and accrual suggestions, manufacturer scorecards and benchmarks, forecasting, SSO/SCIM, SOC 2. The durable advantages to build toward: a library of manufacturer adapters (claim layouts, remittance parsers, reason-code maps), the immutable agreement/claim history ledger, and later cross-customer benchmarks (anonymised, consented).

## 11. Document map

| File | Purpose |
|---|---|
| `PRODUCT_REQUIREMENTS.md` | Functional/non-functional requirements, user stories, acceptance criteria |
| `TECH_STACK.md` | Approved technologies and rejected alternatives |
| `ARCHITECTURE.md` | Components, modules, flows |
| `DATABASE_SCHEMA.md` | Tables, constraints, ER diagram |
| `API_SPECIFICATION.md` | REST conventions and MVP endpoints |
| `BUSINESS_LOGIC.md` | Calculation rules with worked examples |
| `AI_PROCESSING.md` | Extraction pipeline, guardrails, schemas |
| `SECURITY.md` | Security controls |
| `TESTING_STRATEGY.md` | Test layers, critical edge cases |
| `TEST_DATA_SPEC.md` | Synthetic data catalogue and ground truth |
| `DEPLOYMENT.md` | Environments, Docker, CI/CD, backups |
| `DEVELOPMENT_ROADMAP.md` | Stages and dependencies |
| `IMPLEMENTATION_PLAN.md` | Checkbox task list per stage |
| `PROJECT_STATUS.md` | Live progress tracker |
| `ARCHITECTURE_DECISIONS.md` | ADR log |
| `CODE_REVIEW_CHECKLIST.md` | Stage-completion gate |
