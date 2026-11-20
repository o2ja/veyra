"""files module: uploads, quarantine, malware scan, presigned downloads (FR-FI-01..04). Public API."""

from rebate.core.config import Settings
from rebate.core.errors import InvalidState
from rebate.modules.files import jobs as _jobs  # noqa: F401  (registers job handlers)
from rebate.modules.files import storage
from rebate.modules.files.models import FileObject
from rebate.modules.files.router import router
from rebate.modules.files.service import Use, get, register_use_source
from rebate.modules.files.validation import UndecodableText, decode_text


def read_clean(settings: Settings, row: FileObject) -> bytes:
    """The bytes of a file that passed the malware scan (S3 importers). Nothing else is readable."""
    if row.scan_status != "clean":
        raise InvalidState("Only files that passed the malware scan can be read.")
    return b"".join(storage.read_chunks(settings, row.storage_key))


__all__ = [
    "FileObject",
    "UndecodableText",
    "Use",
    "decode_text",
    "get",
    "read_clean",
    "register_use_source",
    "router",
]
