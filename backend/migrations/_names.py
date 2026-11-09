import re

_IDENT = re.compile(r"^[a-z_][a-z0-9_]{0,62}(\.[a-z_][a-z0-9_]{0,62})?$")


def checked_table(table: str) -> str:
    """Migration helpers interpolate table names into DDL; allow only plain lowercase identifiers."""
    if not _IDENT.fullmatch(table):
        raise ValueError(f"invalid table name: {table!r}")
    return table


def bare(table: str) -> str:
    return table.rsplit(".", 1)[-1]
