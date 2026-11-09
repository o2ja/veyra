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

_TENANT_TABLES = """
    WITH tenant_tables AS (
        SELECT c.oid, c.relname, c.relrowsecurity, c.relforcerowsecurity, a.attnum AS tid
        FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        JOIN pg_attribute a ON a.attrelid = c.oid AND a.attname = 'tenant_id' AND NOT a.attisdropped
        WHERE n.nspname = :schema AND c.relkind IN ('r', 'p')
    )
"""

_RLS = text(
    _TENANT_TABLES
    + """
    SELECT format('%s: row level security not enabled and forced', relname)
    FROM tenant_tables WHERE NOT (relrowsecurity AND relforcerowsecurity)
    """
)

_COMPOSITE_UNIQUE = text(
    _TENANT_TABLES
    + """
    SELECT format('%s: missing UNIQUE (tenant_id, id)', tt.relname)
    FROM tenant_tables tt
    WHERE NOT EXISTS (
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
        violations += conn.execute(query, {"schema": schema}).scalars().all()
    return sorted(violations)
