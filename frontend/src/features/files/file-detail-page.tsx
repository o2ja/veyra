import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";

import { Alert, Button, Card, ErrorAlert, Loading, PermissionDenied } from "../../components/ui";
import { api } from "../../lib/api/client";
import { ApiError, unwrap } from "../../lib/api/errors";
import { formatDateTime } from "../../lib/format";
import { useSession } from "../../lib/session";
import { ConfirmDialog } from "../claims/common";
import { STATUS_TEXT } from "./files-page";

const USED_BY_ROUTE: Record<string, (id: string) => string> = {
  import: (id) => `/imports/${id}`,
  extraction: (id) => `/extractions/${id}`,
  agreement_draft: (id) => `/agreement-drafts/${id}`,
  agreement_version: (id) => `/agreements/versions/${id}`,
  remittance: (id) => `/reconciliation/${id}`,
  case_attachment: (id) => `/recovery/${id}`,
  claim_batch: (id) => `/claims/batches/${id}`,
};

/** One file's metadata and scan result (S2.3). Polls while the scan is pending. */
export function FileDetailPage() {
  const { fileId = "" } = useParams();
  const { can } = useSession();
  const navigate = useNavigate();
  const client = useQueryClient();
  const [removing, setRemoving] = useState(false);
  const file = useQuery({
    queryKey: ["files", fileId],
    queryFn: () => unwrap(api.GET("/api/v1/files/{file_id}", { params: { path: { file_id: fileId } } })),
    refetchInterval: (q) => (q.state.data?.scan_status === "pending" ? 2000 : false),
  });
  const download = useMutation({
    mutationFn: () =>
      unwrap(api.GET("/api/v1/files/{file_id}/download-url", { params: { path: { file_id: fileId } } })),
    onSuccess: (r) => window.location.assign(r.url),
  });
  const readWithAi = useMutation({
    mutationFn: () =>
      unwrap(
        api.POST("/api/v1/document-extractions", {
          body: { file_id: fileId, kind: "agreement", rerun: false },
        }),
      ),
    onSuccess: (created) => {
      if (created?.id) navigate(`/extractions/${created.id}`);
    },
  });
  const remove = useMutation({
    mutationFn: () =>
      unwrap(api.DELETE("/api/v1/files/{file_id}", { params: { path: { file_id: fileId } } })),
    onSuccess: (r) => {
      setRemoving(false);
      void client.invalidateQueries({ queryKey: ["files"] });
      if (r.result === "deleted") navigate("/files");
    },
  });
  const f = file.data;
  const usedBy = f?.used_by ?? [];
  if ((file.error as ApiError)?.status === 403) return <PermissionDenied />;
  const clean = f?.scan_status === "clean";

  return (
    <Card
      title={f?.original_name ?? "File"}
      actions={
        f ? (
          <div className="flex flex-wrap gap-2">
            {can("file.upload") && !f.archived_at && f.scan_status !== "pending" && (
              <Button variant="secondary" onClick={() => setRemoving(true)}>
                {usedBy.length ? "Archive" : "Delete"}
              </Button>
            )}
            {clean && f.kind === "agreement" && can("agreement.draft") && (
              <Button variant="secondary" disabled={readWithAi.isPending} onClick={() => readWithAi.mutate()}>
                {readWithAi.isPending ? "Starting…" : "Read with AI"}
              </Button>
            )}
            {clean && (
              <Button disabled={download.isPending} onClick={() => download.mutate()}>
                Download
              </Button>
            )}
          </div>
        ) : undefined
      }
    >
      <ErrorAlert
        error={
          (file.error as ApiError | null) ??
          (download.error as ApiError | null) ??
          (readWithAi.error as ApiError | null) ??
          (remove.error as ApiError | null)
        }
      />
      {f?.archived_at && (
        <div className="mb-4">
          <Alert tone="info">
            Archived {formatDateTime(f.archived_at)}. Hidden from the file list; records that use it still
            work.
          </Alert>
        </div>
      )}
      <ConfirmDialog
        open={removing}
        onOpenChange={setRemoving}
        title={usedBy.length ? "Archive this file?" : "Delete this file?"}
        description={
          usedBy.length
            ? "Other records use this file, so it can't be deleted. Archiving hides it from the file list and can't be undone. This is recorded in the audit log."
            : "Nothing uses this file. It is deleted for good. This is recorded in the audit log."
        }
        confirmLabel={usedBy.length ? "Archive file" : "Delete file"}
        confirmVariant="danger"
        busy={remove.isPending}
        onConfirm={() => remove.mutate()}
      />
      {file.isPending && <Loading />}
      {f && (
        <dl className="grid grid-cols-[max-content_1fr] gap-x-6 gap-y-2 text-sm">
          <div className="contents">
            <dt className="text-muted">Status</dt>
            <dd>
              <span role="status">{STATUS_TEXT[f.scan_status] ?? f.scan_status}</span>
            </dd>
          </div>
          {f.scan_detail ? (
            <div className="contents">
              <dt className="text-muted">Scan result</dt>
              <dd className="font-mono">{f.scan_detail}</dd>
            </div>
          ) : null}
          <div className="contents">
            <dt className="text-muted">Type</dt>
            <dd>{f.kind}</dd>
          </div>
          <div className="contents">
            <dt className="text-muted">Detected format</dt>
            <dd>{f.detected_mime}</dd>
          </div>
          <div className="contents">
            <dt className="text-muted">Size</dt>
            <dd>{f.size_bytes} bytes</dd>
          </div>
          <div className="contents">
            <dt className="text-muted">SHA-256</dt>
            <dd className="break-all font-mono text-xs">{f.sha256}</dd>
          </div>
          <div className="contents">
            <dt className="text-muted">Uploaded</dt>
            <dd>{formatDateTime(f.created_at)}</dd>
          </div>
          <div className="contents">
            <dt className="text-muted">Scanned</dt>
            <dd>{formatDateTime(f.scanned_at)}</dd>
          </div>
          <div className="contents">
            <dt className="text-muted">Used by</dt>
            <dd>
              {usedBy.length === 0 ? (
                <span className="text-muted">Nothing yet</span>
              ) : (
                <ul className="flex flex-col gap-1">
                  {usedBy.map((u) => {
                    const route = USED_BY_ROUTE[u.kind];
                    return (
                      <li key={`${u.kind}-${u.id}`}>
                        {route ? (
                          <Link to={route(u.id)} className="text-brand underline">
                            {u.label}
                          </Link>
                        ) : (
                          u.label
                        )}
                      </li>
                    );
                  })}
                </ul>
              )}
            </dd>
          </div>
        </dl>
      )}
      <Link to="/files" className="mt-4 inline-block text-brand underline">
        Back to files
      </Link>
    </Card>
  );
}
