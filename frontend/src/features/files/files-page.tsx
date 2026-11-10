import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useRef, useState } from "react";

import type { components } from "../../api/schema";
import { Alert, Button, Card, Empty, ErrorAlert, Loading } from "../../components/ui";
import { api, ensureCsrf, readCsrfCookie } from "../../lib/api/client";
import { ApiError, toApiError, unwrap } from "../../lib/api/errors";
import { useSession } from "../../lib/session";

type FileOut = components["schemas"]["FileOut"];
type Kind = "import" | "agreement" | "remittance" | "attachment";

const KIND_TYPES: Record<Kind, string> = {
  import: ".csv,.xlsx",
  agreement: ".pdf,.xlsx",
  remittance: ".pdf,.csv,.xlsx",
  attachment: ".pdf,.csv,.xlsx",
};

const STATUS_TEXT: Record<string, string> = {
  pending: "Scanning…",
  clean: "Clean",
  infected: "Blocked: malware found",
  error: "Scan failed",
};

/** XHR upload: fetch has no upload progress. Same-origin, CSRF header like every mutating call. */
async function uploadWithProgress(
  file: File,
  kind: Kind,
  onProgress: (pct: number) => void,
): Promise<FileOut> {
  await ensureCsrf();
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", "/api/v1/files");
    xhr.withCredentials = true;
    xhr.setRequestHeader("X-CSRF-Token", readCsrfCookie() ?? "");
    xhr.upload.onprogress = (e) => e.lengthComputable && onProgress(Math.round((e.loaded / e.total) * 100));
    xhr.onload = () => {
      let body: unknown;
      try {
        body = JSON.parse(xhr.responseText);
      } catch {
        body = null;
      }
      if (xhr.status === 201) resolve(body as FileOut);
      else reject(toApiError(new Response(null, { status: xhr.status }), body));
    };
    xhr.onerror = () =>
      reject(new ApiError(0, "network_error", "The upload failed. Check your connection.", [], null));
    const form = new FormData();
    form.append("kind", kind);
    form.append("file", file);
    xhr.send(form);
  });
}

function UploadForm({ onDone }: { onDone: () => void }) {
  const input = useRef<HTMLInputElement>(null);
  const [kind, setKind] = useState<Kind>("import");
  const [progress, setProgress] = useState<number | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const [done, setDone] = useState<string | null>(null);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    const file = input.current?.files?.[0];
    if (!file) return;
    setError(null);
    setDone(null);
    setProgress(0);
    try {
      const f = await uploadWithProgress(file, kind, setProgress);
      setDone(f.original_name);
      if (input.current) input.current.value = "";
      onDone();
    } catch (e) {
      setError(e instanceof ApiError ? e : null);
    } finally {
      setProgress(null);
    }
  }

  return (
    <form className="flex max-w-2xl flex-col gap-3" onSubmit={submit}>
      <div className="flex flex-col gap-3 sm:flex-row sm:items-end">
        <div className="flex flex-col gap-1">
          <label htmlFor="upload-kind" className="text-sm font-medium">
            Type
          </label>
          <select
            id="upload-kind"
            className="rounded-md border border-line bg-panel px-3 py-2 text-sm"
            value={kind}
            onChange={(e) => setKind(e.target.value as Kind)}
          >
            <option value="import">Sales or purchase data (CSV, XLSX)</option>
            <option value="agreement">Agreement (PDF, XLSX)</option>
            <option value="remittance">Remittance (PDF, CSV, XLSX)</option>
            <option value="attachment">Other attachment (PDF, CSV, XLSX)</option>
          </select>
        </div>
        <div className="flex flex-1 flex-col gap-1">
          <label htmlFor="upload-file" className="text-sm font-medium">
            File
          </label>
          <input
            id="upload-file"
            ref={input}
            type="file"
            accept={KIND_TYPES[kind]}
            required
            className="text-sm"
          />
        </div>
        <Button type="submit" disabled={progress !== null}>
          Upload
        </Button>
      </div>
      {progress !== null && (
        <progress className="w-full" max={100} value={progress} aria-label="Upload progress">
          {progress}%
        </progress>
      )}
      {done && (
        <Alert tone="ok">{done} uploaded. It is being scanned for malware before it can be used.</Alert>
      )}
      <ErrorAlert error={error} />
    </form>
  );
}

export function FilesPage() {
  const client = useQueryClient();
  const { can } = useSession();
  const [downloadError, setDownloadError] = useState<ApiError | null>(null);
  const files = useQuery({
    queryKey: ["files"],
    queryFn: () => unwrap(api.GET("/api/v1/files")),
    // poll while any scan is still running
    refetchInterval: (q) => (q.state.data?.data.some((f) => f.scan_status === "pending") ? 2000 : false),
  });

  async function download(id: string) {
    setDownloadError(null);
    try {
      const r = await unwrap(
        api.GET("/api/v1/files/{file_id}/download-url", { params: { path: { file_id: id } } }),
      );
      window.location.assign(r.url);
    } catch (e) {
      setDownloadError(e instanceof ApiError ? e : null);
    }
  }

  return (
    <div className="flex flex-col gap-6">
      {can("file.upload") && (
        <Card title="Upload a file">
          <UploadForm onDone={() => void client.invalidateQueries({ queryKey: ["files"] })} />
        </Card>
      )}
      <Card title="Files">
        <ErrorAlert error={(files.error as ApiError | null) ?? downloadError} />
        {files.isPending ? (
          <Loading />
        ) : !files.data?.data.length ? (
          <Empty>No files yet.</Empty>
        ) : (
          <table className="w-full text-left text-sm">
            <thead className="text-muted">
              <tr>
                <th className="py-2">Name</th>
                <th>Type</th>
                <th>Size</th>
                <th>Status</th>
                <th>
                  <span className="sr-only">Actions</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {files.data.data.map((f) => (
                <tr key={f.id} className="border-t border-line">
                  <td className="py-2">{f.original_name}</td>
                  <td>{f.kind}</td>
                  <td>{(f.size_bytes / 1024).toFixed(1)} KB</td>
                  <td
                    role="status"
                    className={
                      f.scan_status === "clean"
                        ? "text-ok"
                        : f.scan_status === "pending"
                          ? "text-muted"
                          : "text-danger"
                    }
                  >
                    {STATUS_TEXT[f.scan_status] ?? f.scan_status}
                  </td>
                  <td>
                    {f.scan_status === "clean" && (
                      <button
                        type="button"
                        className="text-brand underline"
                        onClick={() => void download(f.id)}
                      >
                        Download
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>
    </div>
  );
}
