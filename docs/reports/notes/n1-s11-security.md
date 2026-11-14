# N1 / S11.3 security fixes: notes and choices

Branch `s11-security` (from main 9443516). Source: `docs/reports/S11_3_ASVS_L2_REVIEW.md`.

## Done
1. **DB parameters in logs.** `make_engine` sets `hide_parameters=True`; the API error middleware logs `error=<ExceptionType>` (+ request id from the log context), not the traceback/message (psycopg also puts row values in messages, so hiding parameters alone is not enough). Test: `test_unhandled_db_error_logs_type_and_request_id_not_parameters`.
2. **Parser limits.** `compose.prod.yml` worker: `mem_limit` (`WORKER_MEM_LIMIT`, 3g), `cpus` (`WORKER_CPUS`, 2), `pids_limit` 256; documented in DEPLOYMENT.md section 3. `MAX_UNCOMPRESSED` 300 MB to 100 MB (openpyxl inflates shared strings roughly 10x into Python objects; 300 MB exceeded any sane worker limit; real workbooks are far below 100 MB). No rlimits added (stdlib `resource` is not portable and OCR already has them). Tests: `archive_too_large`, compose limits present.
3. **Headers.** API headers asserted on 204/401/404/500; Caddyfile parsed (text) and asserted for both site blocks. Container note: the Caddy test reads `infra/` relative to the repo root; a one-off container mounting only `backend/` needs `-v <repo>/infra:/infra:ro` (CI runs on the checkout and needs nothing).
4. **Matrix.** `DOC_PERMISSIONS` (SECURITY.md section 3) and `DOC_ROUTES` (API_SPECIFICATION role column) in `tests/api/test_route_access.py`. Registry equals the docs; nothing weakened. `manufacturer_settings.write` (A P) comes from API_SPECIFICATION 2.3, absent from the SECURITY.md table (docs gap, not changed). `claim-batches` and `remittances` routes are not on main, so not asserted yet (add when S7/S8 merge).
5. **Password change revokes other sessions.** Test added; code already correct.
6. **CSV formula guard.** The errors export currently contains no raw cell values (column and message are system text), so the guard cannot be reached with user input today. Test drives the real `_csv_lines` with all six trigger characters. Guard kept as defence in depth.
7. **Uploads.** Validator did not refuse three cases; fixed in `files/validation.py`: XML DTD/ENTITY in any `.xml`/`.rels` part (`xml_dtd`, bounded 64 KB prolog read; padding beyond that is left to defusedxml), PDF/ZIP polyglot (ZIP end-of-central-directory record in the PDF tail, `content_type_mismatch`). Encrypted PDF: by design it is accepted at upload and quarantined by the ClamAV scan (`AlertEncrypted`, scan_status infected); already tested by `tests/extraction/test_failures.py::test_encrypted_pdf_never_gets_past_the_upload_scan`, so no validator change and no new test (a first attempt to refuse it at upload broke that test and was reverted). Known cost of the polyglot check: a PDF embedding a raw ZIP attachment is refused.
8. **Login per-IP limit.** `login:ip:{ip}` via `rate_limit_counters`, config `login_ip_limit_per_minute` (env `LOGIN_IP_LIMIT_PER_MINUTE`, default 20/min). Per-IP+email 5/min unchanged. E2E uses a random X-Forwarded-For per test, so it is unaffected; the e2e suite could NOT be run from this branch (the shared stack runs main).
9. **Docs.** SECURITY.md section 4 download row and login row updated; K-10 closed in PROJECT_STATUS (config asserted, not verified on a live host).

## Skipped / logged
- ESLint security plugin, Dependabot: new dependency or service, owner decision.
- Edge (Caddy) rate limiting: hosting (OD-3/OD-4); Caddy core has no limiter.
- Finding 9 (session row `FOR UPDATE` serialises one user's requests): known issue, not changed.
- Running the SPA under the CSP / ZAP / pen test: need a staging host (owner).

## Housekeeping
- The review doc was committed with one edit: `REBATE_TEST_DB=rebate_test_rev6` on line 8 tripped the gitleaks hook (generic-api-key false positive), reworded to "test database rebate_test_rev6". No `--no-verify`.
