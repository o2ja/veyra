import { useQuery } from "@tanstack/react-query";
import { Link, useParams } from "react-router-dom";

import { Card, ErrorAlert, Loading } from "../../components/ui";
import { api } from "../../lib/api/client";
import { ApiError, unwrap } from "../../lib/api/errors";
import { STATUS_TEXT } from "./files-page";

/** One file's metadata and scan result (S2.3). Polls while the scan is pending. */
export function FileDetailPage() {
  const { fileId = "" } = useParams();
  const file = useQuery({
    queryKey: ["files", fileId],
    queryFn: () => unwrap(api.GET("/api/v1/files/{file_id}", { params: { path: { file_id: fileId } } })),
    refetchInterval: (q) => (q.state.data?.scan_status === "pending" ? 2000 : false),
  });
  const f = file.data;
  return (
    <Card title={f?.original_name ?? "File"}>
      <ErrorAlert error={file.error as ApiError | null} />
      {file.isPending && <Loading />}
      {f && (
        <dl className="grid grid-cols-[max-content_1fr] gap-x-6 gap-y-2 text-sm">
          <dt className="text-muted">Status</dt>
          <dd role="status">{STATUS_TEXT[f.scan_status] ?? f.scan_status}</dd>
          {f.scan_detail && (
            <>
              <dt className="text-muted">Scan result</dt>
              <dd className="font-mono">{f.scan_detail}</dd>
            </>
          )}
          <dt className="text-muted">Type</dt>
          <dd>{f.kind}</dd>
          <dt className="text-muted">Detected format</dt>
          <dd>{f.detected_mime}</dd>
          <dt className="text-muted">Size</dt>
          <dd>{f.size_bytes} bytes</dd>
          <dt className="text-muted">SHA-256</dt>
          <dd className="break-all font-mono text-xs">{f.sha256}</dd>
          <dt className="text-muted">Uploaded (UTC)</dt>
          <dd>{f.created_at.replace("T", " ").slice(0, 19)}</dd>
          <dt className="text-muted">Scanned (UTC)</dt>
          <dd>{f.scanned_at?.replace("T", " ").slice(0, 19) ?? "—"}</dd>
        </dl>
      )}
      <Link to="/files" className="mt-4 inline-block text-brand underline">
        Back to files
      </Link>
    </Card>
  );
}
