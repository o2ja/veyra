"""Field-level scoring of an extraction against ``expected_extraction/*`` (AI_PROCESSING.md §11).

Shared by the CI replay test (recorded responses) and the on-demand live harness (``-m live_llm``).
Comparison is value-level against the hand-written ground truth, never against the code's own output.
"""

from typing import Any

HEADER = ("agreement_type", "external_number", "valid_from", "valid_to")
ITEM = ("reference_cost", "deviated_cost", "sell_price", "rate", "flat_amount", "max_quantity", "claim_uom")


def _rules(rules: dict[str, Any]) -> dict[str, Any]:
    calc = rules.get("calculation") or {}
    flat = {
        "rules.date_basis": rules.get("date_basis"),
        "rules.calculation.basis": calc.get("basis"),
        "rules.calculation.cost_source": calc.get("cost_source"),
        "rules.claim_uom": rules.get("claim_uom"),
        "rules.rounding": rules.get("rounding"),
        "rules.spa_reference.required": (rules.get("spa_reference") or {}).get("required"),
        "rules.volume_basis": rules.get("volume_basis"),
        "rules.tier_mode": rules.get("tier_mode"),
        "rules.spa_lines": rules.get("spa_lines"),
        "rules.measurement_period": rules.get("measurement_period"),
    }
    return {k: v for k, v in flat.items() if v is not None}


def flat(doc: dict[str, Any]) -> dict[str, Any]:
    """A payload or ground-truth object -> ``{comparable key: value}`` (null values dropped)."""
    header = doc.get("header", {})
    out: dict[str, Any] = {f"header.{k}": header[k] for k in HEADER if header.get(k) is not None}
    out.update(_rules(doc.get("rules", {})))
    for c in doc.get("customers", []):
        if c.get("customer_code") is not None and c.get("scope") is not None:
            out[f"customers[{c['customer_code']}].scope"] = c["scope"]
    for i in doc.get("items", []):
        for f in ITEM:
            if i.get(f) is not None:
                out[f"items[{i['sku']}].{f}"] = i[f]
    for t in doc.get("tiers", []):
        if t.get("rate") is not None:
            out[f"tiers[{t['lower_bound']}].rate"] = t["rate"]
    return out


def score(truth: dict[str, Any], payload: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
    expected, got = flat(truth), flat(payload)
    wrong = {k: (got[k], expected.get(k)) for k in got if k in expected and got[k] != expected[k]}
    extra = sorted(k for k in got if k not in expected)  # e.g. a customer name that did not resolve to a code
    tp = sum(1 for k in got if k in expected and got[k] == expected[k])
    fields = result["fields"]
    critical = [f for f in fields if f["critical"]]
    claimed = [f for f in fields if f["pass_a"] and f["pass_a"]["status"] != "absent"]
    return {
        "tp": tp,
        "wrong": wrong,  # hallucination: a value in the draft that contradicts the ground truth
        "extra": extra,
        "missing": sorted(k for k in expected if k not in got),
        "precision": tp / (tp + len(wrong)) if tp + len(wrong) else 1.0,
        "recall": tp / len(expected) if expected else 1.0,
        "fully_correct": not wrong and not [k for k in expected if k not in got],
        "evidence_validity": (
            sum(f["pass_a"]["status"] == "verified" for f in claimed) / len(claimed) if claimed else 1.0
        ),
        "critical_conflict_rate": (
            sum(f["confidence"] == "conflict" for f in critical) / len(critical) if critical else 0.0
        ),
    }
