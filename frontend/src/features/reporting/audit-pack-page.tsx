import { useMutation, useQuery } from "@tanstack/react-query";
import { useState } from "react";

import { Alert, Button, Card, ErrorAlert, Loading, PermissionDenied } from "../../components/ui";
import { api } from "../../lib/api/client";
import { ApiError, unwrap } from "../../lib/api/errors";
import { useSession } from "../../lib/session";
import { ManufacturerSelect } from "../masterdata/common";
import { ReportingNav } from "./common";
import { formatDecimal, type BatchOut } from "./logic";

export function AuditPackPage() {
  const { can } = useSession();
  const canExport = can("audit_pack.export");

  const [manufacturerId, setManufacturerId] = useState("");
  const [selectedBatchId, setSelectedBatchId] = useState("");

  const [exportJobId, setExportJobId] = useState<string | null>(null);
  const [exportFileId, setExportFileId] = useState<string | null>(null);
  const [jobStatus, setJobStatus] = useState<string | null>(null);

  // Batches query: submitted batches for chosen manufacturer
  const batchesQuery = useQuery({
    queryKey: ["claim-batches", "submitted", manufacturerId],
    enabled: Boolean(canExport && manufacturerId),
    queryFn: () =>
      unwrap(
        api.GET("/api/v1/claim-batches", {
          params: {
            query: {
              status: "submitted",
              manufacturer_id: manufacturerId || null,
              limit: 100,
            },
          },
        }),
      ),
  });

  // Job polling
  useQuery({
    queryKey: ["jobs", exportJobId],
    enabled: Boolean(exportJobId),
    queryFn: () => {
      if (!exportJobId) throw new Error("Missing job id");
      return unwrap(
        api.GET("/api/v1/jobs/{job_id}", {
          params: { path: { job_id: exportJobId } },
        }),
      );
    },
    refetchInterval: (query) => {
      const st = query.state.data?.status;
      setJobStatus(st ?? null);
      if (st === "completed" || st === "finished") {
        return false;
      }
      return 1500;
    },
  });

  // Generate audit pack mutation (XLSX only)
  const generateMutation = useMutation({
    mutationFn: async () => {
      if (!selectedBatchId) throw new Error("Please select a claim batch.");
      const res = await unwrap(
        api.GET("/api/v1/reports/claim-batches/{batch_id}/audit-pack", {
          params: {
            path: { batch_id: selectedBatchId },
            query: { format: "xlsx" },
          },
        }),
      );
      return res as unknown as { job?: { id: string }; file_id?: string };
    },
    onSuccess: (data) => {
      if (data?.file_id) {
        setExportFileId(data.file_id);
      }
      if (data?.job?.id) {
        setExportJobId(data.job.id);
        setJobStatus("pending");
      }
    },
  });

  // Download URL mutation
  const downloadMutation = useMutation({
    mutationFn: async () => {
      if (!exportFileId) throw new Error("No file ID available");
      const res = await unwrap(
        api.GET("/api/v1/files/{file_id}/download-url", {
          params: { path: { file_id: exportFileId } },
        }),
      );
      window.open(res.url, "_blank", "noopener");
      return res;
    },
  });

  if (!canExport) {
    return (
      <>
        <ReportingNav />
        <PermissionDenied />
      </>
    );
  }

  const isJobComplete = jobStatus === "completed" || jobStatus === "finished";
  const genErr = generateMutation.error as ApiError | null;

  if (genErr?.status === 403 || genErr?.code === "forbidden") {
    return (
      <>
        <ReportingNav />
        <PermissionDenied />
      </>
    );
  }

  const batches = batchesQuery.data?.data ?? [];

  return (
    <>
      <ReportingNav />

      <div className="flex max-w-2xl flex-col gap-6">
        <div>
          <h1 className="font-semibold text-2xl text-ink">Audit pack export</h1>
          <p className="text-muted text-xs">
            Export a complete audit pack for a submitted claim batch in XLSX format.
          </p>
        </div>

        <Card title="Export parameters">
          <div className="flex flex-col gap-4">
            {/* 1. Manufacturer selection */}
            <div className="flex flex-col gap-1 text-sm">
              <label htmlFor="audit-mfg" className="font-medium text-xs text-muted">
                Manufacturer
              </label>
              <ManufacturerSelect
                value={manufacturerId}
                onChange={(mfg) => {
                  setManufacturerId(mfg);
                  setSelectedBatchId("");
                  setExportJobId(null);
                  setExportFileId(null);
                  setJobStatus(null);
                }}
              />
            </div>

            {/* 2. Submitted Batch selection */}
            {manufacturerId && (
              <div className="flex flex-col gap-1 text-sm">
                <label htmlFor="audit-batch" className="font-medium text-xs text-muted">
                  Submitted claim batch
                </label>
                {batchesQuery.isPending ? (
                  <Loading />
                ) : batches.length === 0 ? (
                  <p className="text-xs text-muted">
                    No submitted claim batches found for this manufacturer.
                  </p>
                ) : (
                  <select
                    id="audit-batch"
                    value={selectedBatchId}
                    onChange={(e) => {
                      setSelectedBatchId(e.target.value);
                      setExportJobId(null);
                      setExportFileId(null);
                      setJobStatus(null);
                    }}
                    className="rounded border border-line bg-surface p-2 text-sm font-mono"
                  >
                    <option value="">Select a submitted batch…</option>
                    {batches.map((b: BatchOut) => (
                      <option key={b.id} value={b.id}>
                        {b.batch_number} ({b.line_count} lines, {formatDecimal(b.total_amount)})
                      </option>
                    ))}
                  </select>
                )}
              </div>
            )}

            {genErr && <ErrorAlert error={genErr} />}

            <div className="pt-2">
              <Button
                disabled={
                  !selectedBatchId || generateMutation.isPending || Boolean(exportJobId && !isJobComplete)
                }
                onClick={() => generateMutation.mutate()}
              >
                {generateMutation.isPending ? "Generating…" : "Generate audit pack (XLSX)"}
              </Button>
            </div>
          </div>
        </Card>

        {/* Export progress & download */}
        {exportJobId && (
          <Card title="Export status">
            <div className="flex flex-col gap-3 text-sm">
              <div className="flex items-center gap-2">
                {!isJobComplete && <Loading />}
                <span className="font-medium">Job status:</span>
                <span className="font-mono text-xs capitalize">{jobStatus ?? "Queued"}</span>
              </div>

              {isJobComplete && exportFileId && (
                <div className="mt-2 flex flex-col gap-2">
                  <Alert tone="ok">Audit pack generated successfully in XLSX format.</Alert>
                  <div>
                    <Button
                      variant="secondary"
                      disabled={downloadMutation.isPending}
                      onClick={() => downloadMutation.mutate()}
                    >
                      {downloadMutation.isPending ? "Preparing download…" : "Download audit pack (XLSX)"}
                    </Button>
                  </div>
                </div>
              )}
            </div>
          </Card>
        )}
      </div>
    </>
  );
}
