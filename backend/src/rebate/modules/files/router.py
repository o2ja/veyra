"""Files API (API_SPECIFICATION.md §2.2)."""

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Form, Query, Request, UploadFile
from pydantic import BaseModel, ConfigDict

from rebate.core.access import require, tenant_of
from rebate.core.request import Principal, ctx
from rebate.modules.files import service

router = APIRouter(tags=["files"])

Kind = Literal["import", "agreement", "remittance", "attachment"]


class FileOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    kind: str
    original_name: str
    size_bytes: int
    sha256: str
    detected_mime: str
    scan_status: str
    scan_detail: str | None
    uploaded_by: UUID
    created_at: datetime
    scanned_at: datetime | None
    archived_at: datetime | None


class UseOut(BaseModel):
    kind: Literal[
        "import",
        "extraction",
        "agreement_draft",
        "agreement_version",
        "remittance",
        "case_attachment",
        "claim_batch",
    ]
    id: UUID
    label: str


class FileDetail(FileOut):
    used_by: list[UseOut]


class FileRemoved(BaseModel):
    result: Literal["deleted", "archived"]


class FileList(BaseModel):
    data: list[FileOut]
    next_before: UUID | None


class DownloadUrl(BaseModel):
    url: str
    expires_in_seconds: int


@router.post("/files", status_code=201, operation_id="file_upload")
def upload_file(
    request: Request,
    file: UploadFile,
    kind: Annotated[Kind, Form()],
    principal: Annotated[Principal, Depends(require("file.upload"))],
) -> FileOut:
    row = service.upload(
        ctx(request),
        principal,
        tenant_of(principal),
        service.Upload(
            kind=kind, filename=file.filename or "", declared_mime=file.content_type, body=file.file
        ),
    )
    return FileOut.model_validate(row)


@router.get("/files", operation_id="file_list")
def list_files(
    request: Request,
    principal: Annotated[Principal, Depends(require("data.read"))],
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    before: UUID | None = None,
    include_archived: bool = False,
) -> FileList:
    rows = service.list_files(
        ctx(request).db, principal, tenant_of(principal), limit, before, include_archived
    )
    return FileList(
        data=[FileOut.model_validate(r) for r in rows],
        next_before=rows[-1].id if len(rows) == limit else None,
    )


@router.get("/files/{file_id}", operation_id="file_get")
def get_file(
    request: Request, file_id: UUID, principal: Annotated[Principal, Depends(require("data.read"))]
) -> FileDetail:
    db, tenant_id = ctx(request).db, tenant_of(principal)
    row = service.get(db, tenant_id, file_id)
    service.check_readable(row, principal)
    uses = [UseOut.model_validate(u, from_attributes=True) for u in service.used_by(db, tenant_id, file_id)]
    return FileDetail.model_validate({**FileOut.model_validate(row).model_dump(), "used_by": uses})


@router.delete("/files/{file_id}", operation_id="file_delete")
def delete_file(
    request: Request, file_id: UUID, principal: Annotated[Principal, Depends(require("file.upload"))]
) -> FileRemoved:
    """Unused file: deleted. Used by any record: archived (hidden from the list, still readable)."""
    return FileRemoved(result=service.remove(ctx(request), principal, tenant_of(principal), file_id))


@router.get("/files/{file_id}/download-url", operation_id="file_download_url")
def download_url(
    request: Request, file_id: UUID, principal: Annotated[Principal, Depends(require("data.read"))]
) -> DownloadUrl:
    c = ctx(request)
    url = service.download_url(c, principal, tenant_of(principal), file_id)
    return DownloadUrl(url=url, expires_in_seconds=c.settings.presigned_url_seconds)
