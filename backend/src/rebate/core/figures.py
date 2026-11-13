# ruff: noqa: S608  # the SQL is assembled from module constants only; every value is a bound parameter
"""Dashboard figures as one definition per metric (FR-RP-01, W14): a module registers, per metric, the SELECT of the
rows behind the figure (columns ``id, trace_kind, trace_id, manufacturer_id, branch_id, month, amount, label``). The
refreshed monthly summary, the drill-down rows and the drill-down total are all derived from that one SELECT, so a
figure and the rows behind it cannot disagree (and a test compares them with hand-derived values)."""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from typing import Any
from uuid import UUID

from sqlalchemy import Row, text
from sqlalchemy.orm import Session


@dataclass(frozen=True)
class FigureSet:
    """``sources`` maps a metric name to its row SELECT. Bound parameters available to every SELECT: ``:t`` (tenant),
    ``:tz`` (business time zone), ``:as_of`` (business date the figures are computed for)."""

    sources: Mapping[str, str]

    def by_month(self, db: Session, tenant: UUID, metric: str, tz: str, as_of: date) -> list[Row[Any]]:
        sql = (
            f"SELECT manufacturer_id, branch_id, month, sum(amount) AS amount FROM ({self.sources[metric]}) x "
            "GROUP BY manufacturer_id, branch_id, month"
        )
        return list(db.execute(text(sql), {"t": tenant, "tz": tz, "as_of": as_of}))

    def rows(
        self,
        db: Session,
        tenant: UUID,
        metric: str,
        tz: str,
        as_of: date,
        flt: Filter,
        after: UUID | None,
        limit: int,
    ) -> list[Row[Any]]:
        where, params = flt.sql()
        sql = (
            f"SELECT * FROM ({self.sources[metric]}) x WHERE {where} AND (CAST(:after AS uuid) IS NULL OR id > :after) "
            "ORDER BY id LIMIT :n"
        )
        return list(
            db.execute(
                text(sql), {"t": tenant, "tz": tz, "as_of": as_of, "after": after, "n": limit, **params}
            )
        )

    def total(self, db: Session, tenant: UUID, metric: str, tz: str, as_of: date, flt: Filter) -> Any:
        where, params = flt.sql()
        sql = f"SELECT COALESCE(sum(amount), 0) FROM ({self.sources[metric]}) x WHERE {where}"
        return db.execute(text(sql), {"t": tenant, "tz": tz, "as_of": as_of, **params}).scalar_one()


@dataclass(frozen=True)
class Filter:
    manufacturer_id: UUID | None = None
    branch_id: UUID | None = None
    month: date | None = None  # first day of the month

    def sql(self) -> tuple[str, dict[str, Any]]:
        clauses = ["TRUE"]
        params: dict[str, Any] = {}
        for column, value in (
            ("manufacturer_id", self.manufacturer_id),
            ("branch_id", self.branch_id),
            ("month", self.month),
        ):
            if value is not None:
                clauses.append(f"{column} = :f_{column}")
                params[f"f_{column}"] = value
        return " AND ".join(clauses), params
