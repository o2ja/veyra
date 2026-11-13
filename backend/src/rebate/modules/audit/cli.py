"""Operator CLI (runbook ``audit-seal-verify``). Runs as the application role, inside one tenant's context.

python -m rebate.modules.audit.cli verify --tenant <uuid> [--anchor <seal_hash>]   exit 1 on a broken chain
python -m rebate.modules.audit.cli retention-report --tenant <uuid>   dry run, deletes nothing
"""

import argparse
import json
import sys
from dataclasses import asdict
from uuid import UUID

from rebate.core import clock
from rebate.core.config import get_settings
from rebate.core.db import make_engine, transaction
from rebate.modules.audit import seal


def main(argv: list[str] | None = None, engine: object | None = None) -> int:
    ap = argparse.ArgumentParser(prog="rebate.modules.audit.cli")
    ap.add_argument("command", choices=["verify", "retention-report"])
    ap.add_argument("--tenant", type=UUID, required=True)
    ap.add_argument("--anchor", help="verify: a seal_hash kept off-box; fails unless the chain contains it")
    args = ap.parse_args(argv)
    eng = engine or make_engine(get_settings().database_url.get_secret_value())
    with transaction(eng, args.tenant) as db:  # type: ignore[arg-type]
        if args.command == "retention-report":
            print(json.dumps(seal.retention_report(db, args.tenant, clock.now()), indent=2))
            return 0
        result = seal.verify_tenant(db, args.tenant, args.anchor and args.anchor.lower())
    print(json.dumps(asdict(result), indent=2))
    return 0 if result.ok else 1


if __name__ == "__main__":
    sys.exit(main())
