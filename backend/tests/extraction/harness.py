"""Evaluation harness (AI_PROCESSING.md §11): run the golden documents through the full job and aggregate
field-level metrics. The same code scores recorded responses in CI and a live model on demand."""

from typing import Any

from tests.extraction.evaluate import score
from tests.extraction.golden import ground_truth
from tests.extraction.support import draft_of, extract, golden_file
from tests.extraction.test_golden import BRIGHT, COPPER, INJECT, MISSING, REB_NWND, REB_VOLT, SPA_884
from tests.integration.conftest import Workspace

STEMS = (SPA_884, REB_VOLT, REB_NWND, COPPER, INJECT, MISSING, BRIGHT)


def run_all(ws: Workspace, stems: tuple[str, ...] = STEMS) -> dict[str, Any]:
    per_doc: dict[str, Any] = {}
    for stem in stems:
        ex = extract(ws, golden_file(ws, stem), rerun=True)
        if ex["status"] != "completed":
            per_doc[stem] = {"status": ex["status"], "error_code": ex["error_code"]}
            continue
        s = score(ground_truth(stem), draft_of(ws, ex)["payload"], ex["result"])
        per_doc[stem] = {
            "status": "completed",
            "tp": s["tp"],
            "hallucinated_values": len(s["wrong"]),
            "missing": s["missing"],
            "unresolved_names": s["extra"],
            "precision": round(s["precision"], 4),
            "recall": round(s["recall"], 4),
            "fully_correct": s["fully_correct"],
            "evidence_validity": round(s["evidence_validity"], 4),
            "critical_conflict_rate": round(s["critical_conflict_rate"], 4),
            "draft_confidence": ex["confidence"],
        }
    done = [d for d in per_doc.values() if d["status"] == "completed"]
    total_tp = sum(d["tp"] for d in done)
    total_wrong = sum(d["hallucinated_values"] for d in done)
    total_missing = sum(len(d["missing"]) for d in done)
    return {
        "documents": per_doc,
        "summary": {
            "documents": len(per_doc),
            "completed": len(done),
            "fully_correct_documents": sum(d["fully_correct"] for d in done),
            "hallucinated_values": total_wrong,
            "precision": round(total_tp / (total_tp + total_wrong), 4) if total_tp + total_wrong else 1.0,
            "recall": round(total_tp / (total_tp + total_missing), 4) if total_tp + total_missing else 1.0,
            "evidence_validity": round(sum(d["evidence_validity"] for d in done) / len(done), 4)
            if done
            else 0.0,
            "critical_conflict_rate": (
                round(sum(d["critical_conflict_rate"] for d in done) / len(done), 4) if done else 0.0
            ),
        },
    }
