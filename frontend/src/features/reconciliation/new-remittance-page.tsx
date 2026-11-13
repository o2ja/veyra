import { useMutation, useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { useForm } from "react-hook-form";
import { useNavigate } from "react-router-dom";
import { z } from "zod";

import { Alert, Button, Card, ErrorAlert, Field, Loading } from "../../components/ui";
import { api } from "../../lib/api/client";
import { ApiError, unwrap } from "../../lib/api/errors";
import { uploadWithProgress } from "../files/files-page";
import { ManufacturerSelect, SelectField } from "../masterdata/common";
import { ReconciliationNav, ValidationDetailsAlert } from "./common";
import {
  REMITTANCE_CHANNELS,
  REMITTANCE_CHANNEL_LABELS,
  REMITTANCE_DOC_TYPES,
  REMITTANCE_DOC_TYPE_LABELS,
  type RemittanceIn,
} from "./logic";

const schema = z.object({
  manufacturer_id: z.string().min(1, "Manufacturer is required"),
  doc_type: z.enum(REMITTANCE_DOC_TYPES),
  channel: z.enum(REMITTANCE_CHANNELS),
  document_number: z.string().optional(),
  document_date: z
    .string()
    .optional()
    .refine((v) => !v || /^\d{4}-\d{2}-\d{2}$/.test(v), {
      message: "Must be YYYY-MM-DD",
    }),
  payment_date: z
    .string()
    .optional()
    .refine((v) => !v || /^\d{4}-\d{2}-\d{2}$/.test(v), {
      message: "Must be YYYY-MM-DD",
    }),
  total_amount: z
    .string()
    .optional()
    .refine((v) => !v || /^-?\d+(\.\d+)?$/.test(v.trim()), {
      message: "Must be a valid decimal string",
    }),
  currency_code: z.string().optional(),
  mapping_template_id: z.string().optional(),
});

type FormValues = z.infer<typeof schema>;

export function NewRemittancePage() {
  const navigate = useNavigate();
  const [file, setFile] = useState<File | null>(null);
  const [fileId, setFileId] = useState<string | null>(null);
  const [uploadProgress, setUploadProgress] = useState<number | null>(null);
  const [uploadError, setUploadError] = useState<ApiError | null>(null);
  const [jobId, setJobId] = useState<string | null>(null);
  const [targetRemittanceId, setTargetRemittanceId] = useState<string | null>(null);
  const [idemKey] = useState(() => crypto.randomUUID());

  const [manufacturerId, setManufacturerId] = useState("");
  const [docType, setDocType] = useState<FormValues["doc_type"]>("credit_memo");
  const [channel, setChannel] = useState<FormValues["channel"]>("credit_memo");
  const [mfgError, setMfgError] = useState<string | null>(null);

  const {
    register,
    handleSubmit,
    formState: { errors },
  } = useForm<Omit<FormValues, "manufacturer_id" | "doc_type" | "channel">>({
    defaultValues: {
      document_number: "",
      document_date: "",
      payment_date: "",
      total_amount: "",
      currency_code: "",
      mapping_template_id: "",
    },
  });

  // Poll ingest job if one was returned
  const jobQuery = useQuery({
    queryKey: ["jobs", jobId],
    enabled: Boolean(jobId),
    queryFn: () => {
      if (!jobId) throw new Error("Missing job id");
      return unwrap(
        api.GET("/api/v1/jobs/{job_id}", {
          params: { path: { job_id: jobId } },
        }),
      );
    },
    refetchInterval: (query) => {
      const st = query.state.data?.status;
      return st === "running" || st === "queued" || st === "pending" ? 1500 : false;
    },
  });

  // Watch job status
  const jobStatus = jobQuery.data?.status;
  if (jobStatus === "completed" || jobStatus === "finished") {
    if (targetRemittanceId) {
      navigate(`/reconciliation/${targetRemittanceId}`);
    }
  }

  const createRemittance = useMutation({
    mutationFn: async (vals: Omit<FormValues, "manufacturer_id" | "doc_type" | "channel">) => {
      if (!fileId) throw new Error("Please upload a remittance file first.");
      if (!manufacturerId) {
        setMfgError("Manufacturer is required");
        throw new Error("Manufacturer is required");
      }
      setMfgError(null);

      const parsed = schema.safeParse({
        manufacturer_id: manufacturerId,
        doc_type: docType,
        channel,
        ...vals,
      });
      if (!parsed.success) {
        throw new Error(parsed.error.errors[0]?.message ?? "Invalid form data");
      }

      const body: RemittanceIn = {
        file_id: fileId,
        manufacturer_id: manufacturerId,
        doc_type: docType,
        channel: channel,
        document_number: vals.document_number?.trim() || null,
        document_date: vals.document_date?.trim() || null,
        payment_date: vals.payment_date?.trim() || null,
        total_amount: vals.total_amount?.trim() || null,
        currency_code: vals.currency_code?.trim() || null,
        mapping_template_id: vals.mapping_template_id?.trim() || null,
      };

      const res = await unwrap(
        api.POST("/api/v1/remittances", {
          params: { header: { "idempotency-key": idemKey } },
          body,
        }),
      );
      return res as unknown as { remittance: { id: string }; job?: { id: string } };
    },
    onSuccess: (data) => {
      if (data.job?.id) {
        setJobId(data.job.id);
        setTargetRemittanceId(data.remittance.id);
      } else {
        navigate(`/reconciliation/${data.remittance.id}`);
      }
    },
  });

  async function handleFileUpload(selectedFile: File) {
    setFile(selectedFile);
    setUploadError(null);
    setUploadProgress(0);
    try {
      const res = await uploadWithProgress(selectedFile, "remittance", (pct) => {
        setUploadProgress(pct);
      });
      setFileId(res.id);
      setUploadProgress(null);
    } catch (err) {
      setUploadError(err instanceof ApiError ? err : null);
      setUploadProgress(null);
    }
  }

  const createErr = createRemittance.error as ApiError | null;

  return (
    <>
      <ReconciliationNav />

      <div className="flex max-w-2xl flex-col gap-6">
        <Card title="New remittance">
          {jobId && (
            <div className="mb-4 rounded border border-line bg-surface/50 p-4 text-sm">
              <div className="flex items-center gap-2">
                <Loading />
                <span className="font-medium">Ingesting remittance file…</span>
                <span className="text-muted">(Job status: {jobStatus ?? "pending"})</span>
              </div>
            </div>
          )}

          <div className="flex flex-col gap-4">
            {/* 1. File Upload */}
            <div className="flex flex-col gap-1.5">
              <label htmlFor="remittance-file" className="text-sm font-medium">
                Remittance file (CSV, XLSX, PDF) <span className="text-danger">*</span>
              </label>
              <input
                id="remittance-file"
                type="file"
                accept=".csv,.xlsx,.pdf"
                className="text-sm"
                onChange={(e) => {
                  const f = e.target.files?.[0];
                  if (f) void handleFileUpload(f);
                }}
              />
              {uploadProgress !== null && (
                <div className="text-xs text-muted">Uploading: {uploadProgress}%</div>
              )}
              {file && fileId && (
                <div className="text-xs text-ok">
                  ✓ Uploaded: <span className="font-mono">{file.name}</span>
                </div>
              )}
              {uploadError && <ErrorAlert error={uploadError} />}
            </div>

            {createErr && (
              <div>
                {createErr.code === "ai_not_enabled" ? (
                  <Alert tone="danger">
                    AI document extraction is not enabled for this workspace. Please upload a structured CSV
                    or XLSX remittance instead.
                  </Alert>
                ) : createErr.code === "duplicate_remittance" ? (
                  <Alert tone="danger">
                    A remittance with this document number already exists for this manufacturer.
                  </Alert>
                ) : (
                  <ValidationDetailsAlert error={createErr} />
                )}
              </div>
            )}

            {/* 2. Form details */}
            <form onSubmit={handleSubmit((d) => createRemittance.mutate(d))} className="flex flex-col gap-4">
              <ManufacturerSelect
                value={manufacturerId}
                onChange={(val) => {
                  setManufacturerId(val);
                  if (val) setMfgError(null);
                }}
                required
              />
              {mfgError && <p className="text-xs text-danger">{mfgError}</p>}

              <div className="grid gap-3 sm:grid-cols-2">
                <SelectField
                  label="Document type"
                  value={docType}
                  onChange={(e) => setDocType(e.target.value as FormValues["doc_type"])}
                  required
                >
                  {REMITTANCE_DOC_TYPES.map((dt) => (
                    <option key={dt} value={dt}>
                      {REMITTANCE_DOC_TYPE_LABELS[dt] ?? dt}
                    </option>
                  ))}
                </SelectField>

                <SelectField
                  label="Channel"
                  value={channel}
                  onChange={(e) => setChannel(e.target.value as FormValues["channel"])}
                  required
                >
                  {REMITTANCE_CHANNELS.map((ch) => (
                    <option key={ch} value={ch}>
                      {REMITTANCE_CHANNEL_LABELS[ch] ?? ch}
                    </option>
                  ))}
                </SelectField>
              </div>

              <Field
                label="Document number"
                {...register("document_number")}
                error={errors.document_number?.message}
                placeholder="e.g. CM-7781"
              />

              <div className="grid gap-3 sm:grid-cols-2">
                <Field
                  label="Document date"
                  type="date"
                  {...register("document_date")}
                  error={errors.document_date?.message}
                />
                <Field
                  label="Payment date"
                  type="date"
                  {...register("payment_date")}
                  error={errors.payment_date?.message}
                />
              </div>

              <div className="grid gap-3 sm:grid-cols-2">
                <Field
                  label="Total amount"
                  {...register("total_amount")}
                  error={errors.total_amount?.message}
                  placeholder="e.g. 1500.00"
                />
                <Field
                  label="Currency code"
                  {...register("currency_code")}
                  error={errors.currency_code?.message}
                  placeholder="USD"
                />
              </div>

              <div className="mt-2 flex gap-2">
                <Button type="submit" disabled={!fileId || createRemittance.isPending || Boolean(jobId)}>
                  {createRemittance.isPending ? "Submitting…" : "Create and ingest remittance"}
                </Button>
                <Button type="button" variant="secondary" onClick={() => navigate("/reconciliation")}>
                  Cancel
                </Button>
              </div>
            </form>
          </div>
        </Card>
      </div>
    </>
  );
}
