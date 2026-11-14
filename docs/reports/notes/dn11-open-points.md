# D-N11 open points (owner decisions, NOT implemented)

Recorded 2026-11-14 after the independent financial review of branch `fix/dn11-adjustment-allocation`. The D-N11 rule
(a carried negative adjustment is allocated to its own claim line) is implemented; these two consequences are not decided.

## (a) Unpaid carrying batch counts the credit twice

B2 (INV-10020 312.50 + ADJ-2026-0001 -50.00 = 262.50) is overdue and unpaid. Finalising any remittance of the manufacturer
reports INV-10020 `unpaid` 312.50, while CL-4 (paid 200.00 of 250.00 by B1) keeps `short_paid` 50.00.
Claimed - collected = 312.50 (B2 net 262.50 + CL-4's 50.00) but short-paid = 312.50 + 50.00 = 362.50 for the two lines. Same-batch
lines behave the same way (the adjustment is inside the claim line's own batch there, so the effect is the unpaid claim line
only). Question for the owner: should an unpaid carrying batch report unpaid at its net (262.50) or keep the gross?

## (c) Paid 0.00 on the carrying line is not split

A carrying line paid 0.00 (with or without a rejection code) gets no split (no money moved): INV-10020 is `rejected` or
`short_paid` 312.50 AND CL-4 keeps its own `short_paid` 50.00; claimed - collected = 312.50 while short-paid = 362.50: the same
50.00 twice, like the unpaid case (a). A same-batch adjustment would split even at 0.00 (claim line 50.00 paid from nothing),
which was judged worse. Question for the owner: which rule at 0.00?

## (b) Unpaid positive carried adjustment is silent

A positive adjustment (+25.00, e.g. an `agreement_change` of D-N10) carried by an unpaid batch yields no case:
claimed - collected includes 25.00 while short-paid shows 0. If the manufacturer pays the carrying invoice line in full
(312.50, not 337.50), the invoice line shows no case; if it pays the net including the +25.00, the invoice line shows
`overpaid` 25.00 (the positive adjustment is not split off the payment). A positive adjustment cannot be repaired by hand once T2 auto-confirmed the full amount (confirmed matches are immutable).
Question for the owner: how to reconcile and
report a positive carried adjustment (an unpaid one, and a paid one).
