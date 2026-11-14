import { useMutation, useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useParams } from "react-router-dom";

import { Alert, Button, Card, Empty, ErrorAlert, Loading, PermissionDenied } from "../../components/ui";
import { api } from "../../lib/api/client";
import { ApiError, unwrap } from "../../lib/api/errors";
import { KeyValueGrid } from "../calculations/common";
import { utc } from "../masterdata/common";
import { ExtractionsNav } from "./common";
import {
  EXTRACTION_STATUS_LABELS,
  confidenceText,
  formatEvidenceLocation,
  formatExtractionValue,
  groupAndSortFields,
  parseExtractionResult,
  type ExtractionFieldView,
} from "./logic";

function FieldRow({
  field,
  onOpenDocument,
  openingDocument,
}: {
  field: ExtractionFieldView;
  onOpenDocument: (page?: number | null) => void;
  openingDocument: boolean;
}) {
  const [expanded, setExpanded] = useState(false);
  const needsAttention = field.confidence === "conflict" || field.confidence === "unsupported";
  const location = formatEvidenceLocation(field.evidence);
  const pageNum = field.evidence?.page;

  return (
    <div
      className={`rounded-md border p-4 ${needsAttention ? "border-danger bg-surface/60" : "border-line"}`}
    >
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <div className="flex flex-wrap items-center gap-2">
          <span className="font-mono text-sm font-semibold">{field.key}</span>
          {field.critical && (
            <span className="rounded-lg border border-line px-1.5 py-0.5 text-xs font-medium">Critical</span>
          )}
          <span
            className={`rounded border px-1.5 py-0.5 text-xs ${
              needsAttention ? "border-danger font-medium text-danger" : "border-line"
            }`}
          >
            {confidenceText(field.confidence)}
          </span>
        </div>
        {(field.pass_a || field.pass_b) && (
          <Button variant="secondary" aria-expanded={expanded} onClick={() => setExpanded((v) => !v)}>
            {expanded ? "Hide passes" : "Show passes"}
          </Button>
        )}
      </div>

      <p className="mt-2 font-mono text-sm break-all">{formatExtractionValue(field.value)}</p>

      {field.reasons.length > 0 && (
        <ul className="mt-2 list-disc pl-5 text-xs text-danger">
          {field.reasons.map((r, idx) => (
            <li key={`${r}-${idx}`}>{r}</li>
          ))}
        </ul>
      )}

      {field.evidence && (
        <div className="mt-3 rounded border border-line bg-surface/40 p-3 text-xs">
          <div className="flex flex-wrap items-center justify-between gap-2 text-muted">
            <span>{location || "Document evidence"}</span>
            {pageNum !== null && pageNum !== undefined && (
              <button
                type="button"
                disabled={openingDocument}
                className="text-brand underline disabled:opacity-50"
                onClick={() => onOpenDocument(pageNum)}
              >
                Open document at page {pageNum}
              </button>
            )}
          </div>
          {field.evidence.quote && (
            <blockquote className="mt-1.5 whitespace-pre-wrap font-mono text-xs text-ink">
              {field.evidence.quote}
            </blockquote>
          )}
        </div>
      )}

      {expanded && (
        <div className="mt-3 grid gap-3 border-t border-line pt-3 text-xs sm:grid-cols-2">
          <div className="rounded-lg border border-line p-2">
            <h3 className="font-semibold">Pass A</h3>
            {field.pass_a ? (
              <div className="mt-1 space-y-1">
                <p>Status: {field.pass_a.status}</p>
                <p className="font-mono break-all">Value: {formatExtractionValue(field.pass_a.value)}</p>
                {field.pass_a.reasons.length > 0 && (
                  <p className="text-danger">Reasons: {field.pass_a.reasons.join(", ")}</p>
                )}
              </div>
            ) : (
              <p className="mt-1 text-muted">Not recorded.</p>
            )}
          </div>
          <div className="rounded-lg border border-line p-2">
            <h3 className="font-semibold">Pass B</h3>
            {field.pass_b ? (
              <div className="mt-1 space-y-1">
                <p>Status: {field.pass_b.status}</p>
                <p className="font-mono break-all">Value: {formatExtractionValue(field.pass_b.value)}</p>
                {field.pass_b.reasons.length > 0 && (
                  <p className="text-danger">Reasons: {field.pass_b.reasons.join(", ")}</p>
                )}
              </div>
            ) : (
              <p className="mt-1 text-muted">Not recorded.</p>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

export function ExtractionDetailPage() {
  const { extractionId = "" } = useParams();

  const detail = useQuery({
    queryKey: ["document-extractions", extractionId],
    queryFn: () =>
      unwrap(
        api.GET("/api/v1/document-extractions/{extraction_id}", {
          params: { path: { extraction_id: extractionId } },
        }),
      ),
    refetchInterval: (query) => {
      const st = query.state.data?.status;
      return st === "queued" || st === "processing" ? 2000 : false;
    },
  });

  const openDocument = useMutation({
    mutationFn: async (page?: number | null) => {
      const fileId = detail.data?.file_id;
      if (!fileId) return null;
      const res = await unwrap(
        api.GET("/api/v1/files/{file_id}/download-url", {
          params: { path: { file_id: fileId } },
        }),
      );
      const targetUrl = page !== null && page !== undefined ? `${res.url}#page=${page}` : res.url;
      window.open(targetUrl, "_blank", "noopener");
      return res;
    },
  });

  const err = detail.error as ApiError | null;
  const openDocErr = openDocument.error as ApiError | null;

  if (err?.status === 403 || err?.code === "forbidden") {
    return (
      <>
        <ExtractionsNav />
        <PermissionDenied />
      </>
    );
  }

  if (err?.status === 404 || err?.code === "not_found") {
    return (
      <>
        <ExtractionsNav />
        <Card title="Extraction not found">
          <Alert>This document extraction could not be found.</Alert>
          <div className="mt-4">
            <Link to="/extractions" className="text-brand underline">
              Back to extractions
            </Link>
          </div>
        </Card>
      </>
    );
  }

  const rawResult = detail.data?.result;
  const parsed = parseExtractionResult(rawResult);
  const malformedResult = rawResult !== null && rawResult !== undefined && parsed === null;
  const grouped = parsed ? groupAndSortFields(parsed.fields) : { critical: [], other: [] };
  const draftId = detail.data?.draft_id ?? parsed?.draft_id ?? null;

  return (
    <>
      <ExtractionsNav />
      <div className="flex flex-col gap-6">
        <Card
          title={detail.data ? `Extraction ${detail.data.id.slice(0, 8)}` : "Document extraction"}
          actions={
            draftId ? (
              <Link
                to={`/agreement-drafts/${draftId}`}
                className="inline-flex items-center justify-center rounded-md bg-brand px-3 py-2 text-sm font-medium text-white hover:opacity-90"
              >
                Open draft
              </Link>
            ) : undefined
          }
        >
          <ErrorAlert error={err} />
          <ErrorAlert error={openDocErr} />
          {detail.isPending ? (
            <Loading />
          ) : (
            detail.data && (
              <div className="flex flex-col gap-4">
                <dl className="grid gap-3 text-sm sm:grid-cols-2 lg:grid-cols-4">
                  <div>
                    <dt className="text-muted">Status</dt>
                    <dd className="font-medium">
                      {EXTRACTION_STATUS_LABELS[detail.data.status] ?? detail.data.status}
                    </dd>
                  </div>
                  <div>
                    <dt className="text-muted">Confidence</dt>
                    <dd className="font-medium">{detail.data.confidence ?? "—"}</dd>
                  </div>
                  <div>
                    <dt className="text-muted">Model</dt>
                    <dd className="font-mono text-xs">{detail.data.model ?? "—"}</dd>
                  </div>
                  <div>
                    <dt className="text-muted">Prompt version</dt>
                    <dd className="font-mono text-xs">{detail.data.prompt_version ?? "—"}</dd>
                  </div>
                  <div>
                    <dt className="text-muted">Text method</dt>
                    <dd>{detail.data.text_method ?? "—"}</dd>
                  </div>
                  <div>
                    <dt className="text-muted">Created</dt>
                    <dd>{utc(detail.data.created_at)}</dd>
                  </div>
                  <div>
                    <dt className="text-muted">Completed</dt>
                    <dd>{utc(detail.data.completed_at)}</dd>
                  </div>
                  <div>
                    <dt className="text-muted">Document</dt>
                    <dd>
                      <button
                        type="button"
                        disabled={openDocument.isPending}
                        className="text-brand underline disabled:opacity-50"
                        onClick={() => openDocument.mutate(undefined)}
                      >
                        {openDocument.isPending ? "Opening…" : "Open document"}
                      </button>
                    </dd>
                  </div>
                </dl>

                {(detail.data.error_code || detail.data.error_detail) && (
                  <Alert>
                    {[detail.data.error_code, detail.data.error_detail].filter(Boolean).join(": ")}
                  </Alert>
                )}
              </div>
            )
          )}
        </Card>

        {malformedResult && <Alert>Extraction result could not be read.</Alert>}

        {parsed && parsed.indicators.length > 0 && (
          <Card title="Document warning indicators">
            <ul className="flex flex-col gap-3">
              {parsed.indicators.map((ind, idx) => (
                <li key={`${ind.kind}-${idx}`} className="rounded border border-danger p-3 text-sm">
                  <div className="flex flex-wrap items-baseline justify-between gap-2">
                    <span className="font-semibold text-danger">{ind.kind}</span>
                    <span className="text-xs text-muted">{formatEvidenceLocation(ind)}</span>
                  </div>
                  <p className="mt-1 whitespace-pre-wrap font-mono text-xs">{ind.excerpt}</p>
                </li>
              ))}
            </ul>
          </Card>
        )}

        {parsed && (parsed.blocking.length > 0 || parsed.needs_acknowledgement.length > 0) && (
          <Card title="Review requirements">
            <div className="grid gap-4 sm:grid-cols-2">
              <div>
                <h2 className="mb-2 text-sm font-semibold text-danger">Blocking</h2>
                {parsed.blocking.length === 0 ? (
                  <Empty>None.</Empty>
                ) : (
                  <ul className="list-disc pl-5 text-sm">
                    {parsed.blocking.map((b, idx) => (
                      <li key={`${b}-${idx}`}>{b}</li>
                    ))}
                  </ul>
                )}
              </div>
              <div>
                <h2 className="mb-2 text-sm font-semibold">Needs acknowledgement</h2>
                {parsed.needs_acknowledgement.length === 0 ? (
                  <Empty>None.</Empty>
                ) : (
                  <ul className="list-disc pl-5 text-sm">
                    {parsed.needs_acknowledgement.map((n, idx) => (
                      <li key={`${n}-${idx}`}>{n}</li>
                    ))}
                  </ul>
                )}
              </div>
            </div>
          </Card>
        )}

        {parsed && (
          <Card title="Extracted fields">
            {parsed.fields.length === 0 ? (
              <Empty>No fields extracted.</Empty>
            ) : (
              <div className="flex flex-col gap-6">
                {grouped.critical.length > 0 && (
                  <section aria-label="Critical fields" className="flex flex-col gap-3">
                    <h2 className="text-sm font-semibold">Critical fields</h2>
                    {grouped.critical.map((f) => (
                      <FieldRow
                        key={f.key}
                        field={f}
                        openingDocument={openDocument.isPending}
                        onOpenDocument={(page) => openDocument.mutate(page)}
                      />
                    ))}
                  </section>
                )}
                {grouped.other.length > 0 && (
                  <section aria-label="Other fields" className="flex flex-col gap-3">
                    <h2 className="text-sm font-semibold">Other fields</h2>
                    {grouped.other.map((f) => (
                      <FieldRow
                        key={f.key}
                        field={f}
                        openingDocument={openDocument.isPending}
                        onOpenDocument={(page) => openDocument.mutate(page)}
                      />
                    ))}
                  </section>
                )}
              </div>
            )}
          </Card>
        )}

        {parsed && Object.keys(parsed.notes).length > 0 && (
          <Card title="AI note">
            <div className="grid gap-4 sm:grid-cols-2">
              {Object.entries(parsed.notes).map(([passKey, items]) => (
                <div key={passKey}>
                  <h2 className="mb-2 text-sm font-semibold">AI note ({passKey.toUpperCase()})</h2>
                  {items.length === 0 ? (
                    <Empty>No notes.</Empty>
                  ) : (
                    <ul className="list-disc pl-5 text-sm">
                      {items.map((note, idx) => (
                        <li key={`${passKey}-${idx}`} className="whitespace-pre-wrap">
                          {note}
                        </li>
                      ))}
                    </ul>
                  )}
                </div>
              ))}
            </div>
          </Card>
        )}

        {parsed && Object.keys(parsed.unresolved).length > 0 && (
          <Card title="Unresolved items">
            <KeyValueGrid data={parsed.unresolved} />
          </Card>
        )}

        {detail.data?.validation && Object.keys(detail.data.validation).length > 0 && (
          <Card title="Validation summary">
            <KeyValueGrid data={detail.data.validation} />
          </Card>
        )}

        <div>
          <Link to="/extractions" className="text-brand underline">
            Back to extractions
          </Link>
        </div>
      </div>
    </>
  );
}
