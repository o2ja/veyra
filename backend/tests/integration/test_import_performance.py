"""S3.6 performance baseline (deselected by default; run with ``-m performance``).

The 1M-row file comes from ``testdata/generate.py --volume 1000000 --out <scratch>`` (never committed); pass
its path in ``PERF_SALES_CSV``. It exceeds the 50 MB upload cap, so the file object is seeded directly (bytes
in object storage + a ``clean`` file_objects row), which skips only upload and malware scan; parse, validate
and the chunked load run through the real API and job chain. Results are printed for the notes.
"""

import hashlib
import os
import resource
import time
from pathlib import Path

import pytest
from sqlalchemy import Engine, text

from rebate.core.ids import new_id
from rebate.modules.files import storage
from tests.integration.conftest import Workspace, confirm, map_suggested, start

pytestmark = pytest.mark.performance


def _seed_clean_file(ws: Workspace, engine: Engine, path: Path) -> str:
    fid, digest = new_id(), hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            digest.update(chunk)
    key = f"tenants/{ws.tenant_id}/imports/perf/{fid}"
    with path.open("rb") as f:
        storage.put(ws.app.state.settings, key, f)
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO file_objects (tenant_id, id, kind, original_name, storage_key, size_bytes, sha256, "
                "detected_mime, scan_status, scanned_at, uploaded_by) VALUES (:t, :id, 'import', :n, :k, :s, :h, "
                "'text/csv', 'clean', now(), :u)"
            ),
            {
                "t": ws.tenant_id,
                "id": fid,
                "n": path.name,
                "k": key,
                "s": path.stat().st_size,
                "h": digest.hexdigest(),
                "u": ws.user_id,
            },
        )
    return str(fid)


def _rss_mb() -> float:
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024  # Linux: KiB


def test_one_million_sales_rows(ws: Workspace, admin_engine: Engine) -> None:
    source = os.environ.get("PERF_SALES_CSV")
    if not source:
        pytest.fail("set PERF_SALES_CSV to a file made by testdata/generate.py --volume 1000000 --out <dir>")
    path = Path(source)
    # one sales line per data row (the generator writes unique INV-V###### invoices, one line each)
    expected = sum(1 for _ in path.open("rb")) - 1
    fid = _seed_clean_file(ws, admin_engine, path)

    rss0, t0 = _rss_mb(), time.perf_counter()
    batch = start(ws, fid, "sales")
    if batch["needs_mapping"]:
        batch = map_suggested(ws, batch)
    t1 = time.perf_counter()
    assert batch["status"] == "awaiting_confirmation" and batch["counts"]["valid"] == expected, batch
    done = confirm(ws, batch)
    t2 = time.perf_counter()
    assert done["status"] == "completed" and done["counts"]["inserted"] == expected, done
    with admin_engine.connect() as conn:
        n = conn.execute(
            text("SELECT count(*) FROM sales_document_lines WHERE tenant_id = :t AND import_batch_id = :b"),
            {"t": ws.tenant_id, "b": batch["id"]},
        ).scalar_one()
    assert n == expected
    print(
        f"\nPERF rows={expected} bytes={path.stat().st_size} parse+validate={t1 - t0:.1f}s "
        f"({expected / (t1 - t0):.0f} rows/s) load={t2 - t1:.1f}s ({expected / (t2 - t1):.0f} rows/s) "
        f"total={t2 - t0:.1f}s ({expected / (t2 - t0):.0f} rows/s) peak_rss={_rss_mb():.0f}MB (before {rss0:.0f}MB)"
    )
