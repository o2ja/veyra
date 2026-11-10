"""Schema lint (DATABASE_SCHEMA.md §2, §10): returns human-readable violations for one schema."""

from sqlalchemy import Connection, text

_FLOAT_TYPES = text(
    """
    SELECT format('%s.%s: forbidden type %s', c.relname, a.attname,
                  format_type(a.atttypid, a.atttypmod))
    FROM pg_attribute a
    JOIN pg_class c ON c.oid = a.attrelid
    JOIN pg_namespace n ON n.oid = c.relnamespace
    JOIN pg_type t ON t.oid = a.atttypid
    WHERE n.nspname = :schema AND c.relkind IN ('r', 'p', 'v', 'm', 'f')
      AND a.attnum > 0 AND NOT a.attisdropped
      AND ARRAY[t.oid, t.typbasetype, t.typelem]
          && ARRAY['real'::regtype, 'double precision'::regtype, 'money'::regtype]::oid[]
    """
)

# Global by design although they carry tenant_id (DATABASE_SCHEMA.md §2, ADR-005): every read path
# filters tenant_id explicitly and the route isolation tests cover them. Adding a name here needs a
# documented reason.
GLOBAL_TABLES = ("jobs",)

_TENANT_TABLES = """
    WITH tenant_tables AS (
        SELECT c.oid, c.relname, c.relrowsecurity, c.relforcerowsecurity, a.attnum AS tid
        FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        JOIN pg_attribute a ON a.attrelid = c.oid AND a.attname = 'tenant_id' AND NOT a.attisdropped
        WHERE n.nspname = :schema AND c.relkind IN ('r', 'p')
          AND NOT (n.nspname = 'public' AND c.relname = ANY(:global_tables))
    )
"""

# Tables keyed by tenant: those with a tenant_id column, plus `tenants` itself (keyed by id).
_RLS_TABLES = """
    WITH rls_tables AS (
        SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity
        FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = :schema AND c.relkind IN ('r', 'p')
          AND NOT (n.nspname = 'public' AND c.relname = ANY(:global_tables))
          AND (c.relname = 'tenants' OR EXISTS (
              SELECT 1 FROM pg_attribute a
              WHERE a.attrelid = c.oid AND a.attname = 'tenant_id' AND NOT a.attisdropped))
    )
"""

_RLS = text(
    _RLS_TABLES
    + """
    SELECT format('%s: row level security not enabled and forced', relname)
    FROM rls_tables WHERE NOT (relrowsecurity AND relforcerowsecurity)
    """
)

_COMPOSITE_UNIQUE = text(
    _TENANT_TABLES
    + """
    SELECT format('%s: missing UNIQUE (tenant_id, id)', tt.relname)
    FROM tenant_tables tt
    -- Tables without an `id` column (1:1 tenant_settings, idempotency_keys keyed by tenant+key) cannot
    -- be the target of a composite FK on (tenant_id, id), so the rule does not apply to them.
    WHERE EXISTS (SELECT 1 FROM pg_attribute a
                  WHERE a.attrelid = tt.oid AND a.attname = 'id' AND NOT a.attisdropped)
      AND NOT EXISTS (
        SELECT 1 FROM pg_index i
        WHERE i.indrelid = tt.oid AND i.indisunique AND i.indpred IS NULL
          AND (SELECT array_agg(att.attname::text ORDER BY att.attname)
               FROM unnest(i.indkey) k JOIN pg_attribute att
                 ON att.attrelid = tt.oid AND att.attnum = k) = ARRAY['id', 'tenant_id']
    )
    """
)

_COMPOSITE_FK = text(
    _TENANT_TABLES
    + """
    SELECT format('%s: foreign key %s to tenant table %s is not composite on tenant_id',
                  child.relname, k.conname, parent.relname)
    FROM pg_constraint k
    JOIN tenant_tables child ON child.oid = k.conrelid
    JOIN tenant_tables parent ON parent.oid = k.confrelid
    WHERE k.contype = 'f'
      AND NOT EXISTS (SELECT 1 FROM unnest(k.conkey, k.confkey) AS u(ck, pk)
                      WHERE u.ck = child.tid AND u.pk = parent.tid)
    """
)


def lint_schema(conn: Connection, schema: str = "public") -> list[str]:
    violations: list[str] = []
    for query in (_FLOAT_TYPES, _RLS, _COMPOSITE_UNIQUE, _COMPOSITE_FK):
        violations += (
            conn.execute(query, {"schema": schema, "global_tables": list(GLOBAL_TABLES)}).scalars().all()
        )
    return sorted(violations)
