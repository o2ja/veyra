import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useRef, useState } from "react";
import { Link, useNavigate } from "react-router-dom";

import { Alert, Button, Card, ErrorAlert, PermissionDenied } from "../../components/ui";
import { api } from "../../lib/api/client";
import { ApiError, unwrap } from "../../lib/api/errors";
import { useSession } from "../../lib/session";
import { uploadWithProgress } from "../files/files-page";
import { Checkbox, DataTable, SelectField, utc } from "../masterdata/common";
import { ExtractionsNav } from "./common";
import { EXTRACTION_STATUS_LABELS, type ExtractionOut } from "./logic";

export function ExtractionsPage() {
  const { can } = useSession();
  const navigate = useNavigate();
  const client = useQueryClient();
  const fileInput = useRef<HTMLInputElement>(null);

  const [selectedFileId, setSelectedFileId] = useState("");
  const [rerun, setRerun] = useState(false);
  const [uploadProgress, setUploadProgress] = useState<number | null>(null);
  const [uploadError, setUploadError] = useState<ApiError | null>(null);

  const list = useQuery({
    queryKey: ["document-extractions"],
    queryFn: () => unwrap(api.GET("/api/v1/document-extractions", { params: { query: { limit: 50 } } })),
    refetchInterval: (query) => {
      const rows = query.state.data?.data;
      if (!rows) return false;
      return rows.some((r) => r.status === "queued" || r.status === "processing") ? 2000 : false;
    },
  });

  const files = useQuery({
    queryKey: ["files", "agreement-picker"],
    enabled: can("agreement.draft"),
    queryFn: () => unwrap(api.GET("/api/v1/files", { params: { query: { limit: 100 } } })),
  });

  const start = useMutation({
    mutationFn: async () => {
      let fileId = selectedFileId;
      const pickedFile = fileInput.current?.files?.[0];
      if (pickedFile) {
        setUploadError(null);
        setUploadProgress(0);
        try {
          const uploaded = await uploadWithProgress(pickedFile, "agreement", setUploadProgress);
          fileId = uploaded.id;
          setSelectedFileId(uploaded.id);
          if (fileInput.current) fileInput.current.value = "";
          void client.invalidateQueries({ queryKey: ["files"] });
        } catch (e) {
          setUploadError(e instanceof ApiError ? e : null);
          throw e;
        } finally {
          setUploadProgress(null);
        }
      }
      if (!fileId) {
        throw new ApiError(422, "validation_error", "Choose or upload an agreement file.", [], null);
      }
      return unwrap(
        api.POST("/api/v1/document-extractions", {
          body: { file_id: fileId, kind: "agreement", rerun },
        }),
      );
    },
    onSuccess: (created) => {
      void client.invalidateQueries({ queryKey: ["document-extractions"] });
      if (created && typeof created === "object" && "id" in created && created.id) {
        navigate(`/extractions/${created.id}`);
      }
    },
  });

  const listErr = list.error as ApiError | null;
  const startErr = start.error as ApiError | null;

  if (listErr?.status === 403 || listErr?.code === "forbidden") {
    return (
      <>
        <ExtractionsNav />
        <PermissionDenied />
      </>
    );
  }

  const agreementFiles = (files.data?.data ?? []).filter((f) => f.kind === "agreement");
  const aiDisabled = startErr?.code === "ai_not_enabled";
  const rateLimited = startErr?.code === "rate_limited" || startErr?.code === "too_many_extractions";

  return (
    <>
      <ExtractionsNav />
      <div className="flex flex-col gap-6">
        {can("agreement.draft") && (
          <Card title="Start agreement extraction">
            <form
              className="flex max-w-2xl flex-col gap-4"
              onSubmit={(e) => {
                e.preventDefault();
                start.mutate();
              }}
            >
              <SelectField
                label="Existing agreement file"
                value={selectedFileId}
                onChange={(e) => setSelectedFileId(e.target.value)}
              >
                <option value="">Choose an uploaded agreement file…</option>
                {agreementFiles.map((f) => (
                  <option key={f.id} value={f.id}>
                    {f.original_name} ({utc(f.created_at)})
                  </option>
                ))}
              </SelectField>

              <div className="flex flex-col gap-1">
                <label htmlFor="extraction-upload" className="text-sm font-medium">
                  Or upload a new agreement file (PDF or XLSX)
                </label>
                <input
                  id="extraction-upload"
                  ref={fileInput}
                  type="file"
                  accept=".pdf,.xlsx"
                  className="text-sm"
                />
                {uploadProgress !== null && (
                  <p role="status" className="text-xs text-muted">
                    Uploading… {uploadProgress}%
                  </p>
                )}
              </div>

              <Checkbox label="Re-run even if an extraction exists" checked={rerun} onChange={setRerun} />

              {aiDisabled ? (
                <Alert>
                  AI extraction is not enabled for this workspace.{" "}
                  {can("settings.read") ? (
                    <Link to="/admin/settings" className="underline">
                      Enable it in workspace settings
                    </Link>
                  ) : (
                    "Ask a workspace admin to enable it in settings."
                  )}
                </Alert>
              ) : rateLimited ? (
                <Alert>{startErr.message}</Alert>
              ) : (
                <ErrorAlert error={uploadError ?? startErr} />
              )}

              <div>
                <Button type="submit" disabled={start.isPending || uploadProgress !== null}>
                  {start.isPending ? "Starting…" : "Start extraction"}
                </Button>
              </div>
            </form>
          </Card>
        )}

        <Card title="Document extractions">
          <DataTable
            caption="Document extractions"
            loading={list.isPending}
            error={listErr}
            rows={list.data?.data}
            empty="No document extractions yet."
            columns={[
              {
                header: "Extraction",
                cell: (ex: ExtractionOut) => (
                  <Link to={`/extractions/${ex.id}`} className="font-mono text-xs text-brand underline">
                    {ex.id.slice(0, 8)}
                  </Link>
                ),
              },
              {
                header: "Status",
                cell: (ex) => (
                  <span className={ex.status === "failed" ? "font-medium text-danger" : "font-medium"}>
                    {EXTRACTION_STATUS_LABELS[ex.status] ?? ex.status}
                  </span>
                ),
              },
              { header: "Kind", cell: (ex) => ex.kind },
              { header: "Confidence", cell: (ex) => ex.confidence ?? "—" },
              { header: "Model", cell: (ex) => ex.model ?? "—" },
              {
                header: "Draft",
                cell: (ex) =>
                  ex.draft_id ? (
                    <Link to={`/agreement-drafts/${ex.draft_id}`} className="text-brand underline">
                      Open draft
                    </Link>
                  ) : (
                    "—"
                  ),
              },
              {
                header: "Error",
                cell: (ex) =>
                  ex.error_code || ex.error_detail ? (
                    <span className="text-xs text-danger">
                      {[ex.error_code, ex.error_detail].filter(Boolean).join(": ")}
                    </span>
                  ) : (
                    "—"
                  ),
              },
              { header: "Created", cell: (ex) => utc(ex.created_at) },
            ]}
          />
        </Card>
      </div>
    </>
  );
}
