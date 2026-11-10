"""S3 access (ADR-017). The only place that talks to object storage.

* SeaweedFS compatibility: boto3's default request/response checksums are switched to "when_required"
  here and only here (ADR-017 condition). Path-style addressing (no bucket subdomains locally).
* Every write asks for SSE-S3 (``AES256``); the bucket also has default encryption and versioning.
* Keys: quarantine ``quarantine/{tenant}/{file}``; clean ``tenants/{tenant}/{kind}s/{yyyy}/{mm}/{file}``
  (ARCHITECTURE.md §8). Original file names never appear in keys.
* Presigned URLs (<= 5 min) are signed against the public endpoint the browser can reach.
"""

from collections.abc import Iterator
from datetime import datetime
from functools import lru_cache
from typing import IO, TYPE_CHECKING
from uuid import UUID

import boto3
from botocore.config import Config

from rebate.core.config import Settings

if TYPE_CHECKING:
    from mypy_boto3_s3 import S3Client

SSE = {"ServerSideEncryption": "AES256"}


def _client(settings: Settings, endpoint: str | None) -> S3Client:
    if settings.s3_access_key_id is None or settings.s3_secret_access_key is None:
        raise RuntimeError("S3 credentials are not configured")
    return boto3.client(
        "s3",
        endpoint_url=endpoint,
        region_name=settings.s3_region,
        aws_access_key_id=settings.s3_access_key_id.get_secret_value(),
        aws_secret_access_key=settings.s3_secret_access_key.get_secret_value(),
        config=Config(
            signature_version="s3v4",
            s3={"addressing_style": "path"},
            request_checksum_calculation="when_required",  # SeaweedFS (ADR-017)
            response_checksum_validation="when_required",
            retries={"max_attempts": 3, "mode": "standard"},
            connect_timeout=5,
            read_timeout=60,
        ),
    )


@lru_cache(maxsize=4)
def client(settings: Settings) -> S3Client:
    return _client(settings, settings.s3_endpoint_url)


@lru_cache(maxsize=4)
def _presign_client(settings: Settings) -> S3Client:
    return _client(settings, settings.s3_public_endpoint_url or settings.s3_endpoint_url)


def quarantine_key(tenant_id: UUID, file_id: UUID) -> str:
    return f"quarantine/{tenant_id}/{file_id}"


def clean_key(tenant_id: UUID, kind: str, file_id: UUID, at: datetime) -> str:
    return f"tenants/{tenant_id}/{kind}s/{at:%Y}/{at:%m}/{file_id}"


def ensure_bucket(settings: Settings) -> None:
    """Idempotent: bucket with versioning and default SSE-S3 (ARCHITECTURE.md §8)."""
    s3 = client(settings)
    existing = {b["Name"] for b in s3.list_buckets().get("Buckets", [])}
    if settings.s3_bucket not in existing:
        s3.create_bucket(Bucket=settings.s3_bucket)
    s3.put_bucket_versioning(Bucket=settings.s3_bucket, VersioningConfiguration={"Status": "Enabled"})
    s3.put_bucket_encryption(
        Bucket=settings.s3_bucket,
        ServerSideEncryptionConfiguration={
            "Rules": [{"ApplyServerSideEncryptionByDefault": {"SSEAlgorithm": "AES256"}}]
        },
    )


def put(settings: Settings, key: str, body: IO[bytes]) -> None:
    client(settings).upload_fileobj(
        body, settings.s3_bucket, key, ExtraArgs={**SSE, "ContentType": "application/octet-stream"}
    )


def copy(settings: Settings, source: str, target: str) -> None:
    client(settings).copy(
        {"Bucket": settings.s3_bucket, "Key": source},
        settings.s3_bucket,
        target,
        ExtraArgs={**SSE, "ContentType": "application/octet-stream", "MetadataDirective": "REPLACE"},
    )


def delete(settings: Settings, key: str) -> None:
    client(settings).delete_object(Bucket=settings.s3_bucket, Key=key)


def read_chunks(settings: Settings, key: str, size: int = 1024 * 1024) -> Iterator[bytes]:
    body = client(settings).get_object(Bucket=settings.s3_bucket, Key=key)["Body"]
    try:
        yield from body.iter_chunks(chunk_size=size)
    finally:
        body.close()


def presigned_download(settings: Settings, key: str, filename: str, content_type: str) -> str:
    """Attachment download; filename is already sanitised (ASCII, no quotes or control characters)."""
    return _presign_client(settings).generate_presigned_url(
        "get_object",
        Params={
            "Bucket": settings.s3_bucket,
            "Key": key,
            "ResponseContentDisposition": f'attachment; filename="{filename}"',
            "ResponseContentType": content_type,
        },
        ExpiresIn=settings.presigned_url_seconds,
    )
