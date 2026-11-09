"""UUIDv7 primary keys generated in the application (PostgreSQL 16 has no native uuidv7)."""

import uuid

import uuid_utils


def new_id() -> uuid.UUID:
    return uuid.UUID(bytes=uuid_utils.uuid7().bytes)
