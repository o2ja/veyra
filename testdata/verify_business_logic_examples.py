"""SYNTHETIC / documentation check.
Re-computes every worked example in docs/BUSINESS_LOGIC.md with plain Decimal arithmetic
(no application code). Run: python3 -I testdata/verify_business_logic_examples.py
"""
from datetime import date, timedelta
from decimal import Decimal as D, ROUND_HALF_UP, getcontext

getcontext().prec = 28


def r2(x):
    return x.quantize(D("0.01"), rounding=ROUND_HALF_UP)


def check(name, got, want):
    ok = got == want
    print(f"{'OK  ' if ok else 'FAIL'} {name}: got {got} want {want}")
    return ok


results = []
# S1
u = D("100.00") - D("78.00")
results.append(check("S1 unit", u, D("22.00")))
results.append(check("S1 claim", r2(D(100) * u), D("2200.00")))
# S3
u3 = D("100") - D("88") * (D(1) - D("0.12"))
results.append(check("S3 unit", u3, D("22.56")))
results.append(check("S3 claim", r2(D(100) * u3), D("2256.00")))
# S4
u4 = D("88") * D("0.05")
results.append(check("S4 unit", u4, D("4.40")))
results.append(check("S4 claim", r2(D(100) * u4), D("440.00")))
# S5
q5 = D(3) * D(500)
results.append(check("S5 qty", q5, D(1500)))
results.append(check("S5 claim", r2(q5 * D("0.04")), D("60.00")))
# S6
rem = D(100) - D(60)
elig = min(D(60), rem)
results.append(check("S6 A", r2(D(60) * u), D("1320.00")))
results.append(check("S6 B", r2(elig * u), D("880.00")))
results.append(check("S6 excess", D(60) - elig, D(20)))
# S7
per_line = sum(r2(D("0.335")) for _ in range(3))
per_doc = r2(D("0.335") * 3)
results.append(check("S7 per_line", per_line, D("1.02")))
results.append(check("S7 per_doc", per_doc, D("1.01")))
# S8 / S9
results.append(check("S8 adjustment", r2(-(u * 15)), D("-330.00")))
results.append(check("S9 net claim", r2(D(85) * u), D("1870.00")))
# S9a (owner decision 2026-11-13): returned before the claim, re-run after it, then a second return
u9 = D("12.50")
results.append(check("S9a claim net of return", r2((D(20) - 4) * u9), D("200.00")))
results.append(check("S9a later return", r2(-(u9 * 3)), D("-37.50")))
# S10
valid_to = date(2026, 6, 30)
results.append(check("S10 ship_date expired", date(2026, 7, 2) > valid_to, True))
results.append(check("S10 invoice_date ok", date(2026, 6, 28) <= valid_to, True))
# R1
r1, r2t, r3t = D("0.015"), D("0.025"), D("0.035")
earned_990 = r2(D(990000) * r1)
results.append(check("R1 earned 990k", earned_990, D("14850.00")))
earned_1m = r2(D(1000000) * r2t)
results.append(check("R1 delta at 1.0M", earned_1m - earned_990, D("10150.00")))
results.append(check("R1 delta 990k->1.01M", r2(D(1010000) * r2t) - earned_990, D("10400.00")))
# R2
results.append(check("R2 clawback", earned_990 - r2(D(1010000) * r2t), D("-10400.00")))
# R3
results.append(check("R3 qualify_only", r2(D(1000000) * r2t), D("25000.00")))
results.append(check("R3 boundary raw", D("999999.00") * r1, D("14999.985")))
results.append(check("R3 boundary rounded", r2(D("999999.00") * r1), D("14999.99")))
# R4
inc = D(1000000) * r1 + D(500000) * r2t
results.append(check("R4 incremental", r2(inc), D("27500.00")))
results.append(check("R4 retro comparison", r2(D(1500000) * r2t), D("37500.00")))
# D1 / D2
results.append(check("D1", date(2026, 3, 1) + timedelta(days=60), date(2026, 4, 30)))
d2 = date(2026, 5, 15) + timedelta(days=60)
results.append(check("D2", d2, date(2026, 7, 14)))
results.append(check("D2 -14", d2 - timedelta(days=14), date(2026, 6, 30)))
results.append(check("D2 -7", d2 - timedelta(days=7), date(2026, 7, 7)))
results.append(check("D2 -3", d2 - timedelta(days=3), date(2026, 7, 11)))
# D3: period_end basis = last day of the claim-date month + window
results.append(check("D3", date(2026, 3, 31) + timedelta(days=60), date(2026, 5, 30)))
results.append(check("D3 leap", date(2028, 2, 29) + timedelta(days=60), date(2028, 4, 29)))
# V1 / V2
results.append(check("V1 variance", D("2200.00") - D("2050.00"), D("150.00")))
results.append(check("V2 lump", D("2200.00") + D("2230.00"), D("4430.00")))
# A1
results.append(check("A1 adjustment", r2(D(500) * (D("20.00") - D("22.00"))), D("-1000.00")))

print()
print("ALL OK" if all(results) else "FAILURES PRESENT")
raise SystemExit(0 if all(results) else 1)
