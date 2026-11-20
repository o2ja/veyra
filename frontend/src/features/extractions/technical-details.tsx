import { useState } from "react";

import { Alert, Button, Empty } from "../../components/ui";
import { formatDateTime } from "../../lib/format";
import { KeyValueGrid } from "../calculations/common";
import {
  EXTRACTION_STATUS_LABELS,
  confidenceText,
  formatEvidenceLocation,
  formatExtractionValue,
  groupAndSortFields,
  type ExtractionDetail,
  type ExtractionFieldView,
  type ExtractionResult,
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
          {field.payload_path && <span className="font-mono text-xs text-muted">→ {field.payload_path}</span>}
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
            <span>{formatEvidenceLocation(field.evidence) || "Document evidence"}</span>
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
          {(["pass_a", "pass_b"] as const).map((k) => (
            <div key={k} className="rounded-lg border border-line p-2">
              <h3 className="font-semibold">{k === "pass_a" ? "Pass A" : "Pass B"}</h3>
              {field[k] ? (
                <div className="mt-1 space-y-1">
                  <p>Status: {field[k].status}</p>
                  <p className="font-mono break-all">Value: {formatExtractionValue(field[k].value)}</p>
                  {field[k].reasons.length > 0 && (
                    <p className="text-danger">Reasons: {field[k].reasons.join(", ")}</p>
                  )}
                </div>
              ) : (
                <p className="mt-1 text-muted">Not recorded.</p>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

/** For support staff: everything the engine recorded about the read, collapsed so it never leads the page. */
export function TechnicalDetails({
  detail,
  parsed,
  onOpenDocument,
  openingDocument,
}: {
  detail: ExtractionDetail;
  parsed: ExtractionResult | null;
  onOpenDocument: (page?: number | null) => void;
  openingDocument: boolean;
}) {
  const grouped = parsed ? groupAndSortFields(parsed.fields) : { critical: [], other: [] };
  const rows = (fields: ExtractionFieldView[]) =>
    fields.map((f) => (
      <FieldRow key={f.key} field={f} openingDocument={openingDocument} onOpenDocument={onOpenDocument} />
    ));

  return (
    <details className="rounded-xl border border-line bg-panel p-5 shadow-card sm:p-6">
      <summary className="cursor-pointer text-lg font-semibold tracking-tight">Technical details</summary>
      <div className="mt-4 flex flex-col gap-6">
        <dl className="grid gap-3 text-sm sm:grid-cols-2 lg:grid-cols-4">
          <div>
            <dt className="text-muted">Run status</dt>
            <dd className="font-medium">{EXTRACTION_STATUS_LABELS[detail.status] ?? detail.status}</dd>
          </div>
          <div>
            <dt className="text-muted">Stored confidence</dt>
            <dd className="font-mono text-xs">{detail.confidence ?? "—"}</dd>
          </div>
          <div>
            <dt className="text-muted">Model</dt>
            <dd className="font-mono text-xs">{detail.model ?? "—"}</dd>
          </div>
          <div>
            <dt className="text-muted">Prompt version</dt>
            <dd className="font-mono text-xs">{detail.prompt_version ?? "—"}</dd>
          </div>
          <div>
            <dt className="text-muted">Text method</dt>
            <dd>{detail.text_method ?? "—"}</dd>
          </div>
          <div>
            <dt className="text-muted">Started</dt>
            <dd>{formatDateTime(detail.created_at)}</dd>
          </div>
          <div>
            <dt className="text-muted">Finished</dt>
            <dd>{formatDateTime(detail.completed_at)}</dd>
          </div>
          <div>
            <dt className="text-muted">Extraction id</dt>
            <dd className="font-mono text-xs break-all">{detail.id}</dd>
          </div>
        </dl>

        {(detail.error_code || detail.error_detail) && (
          <Alert>{[detail.error_code, detail.error_detail].filter(Boolean).join(": ")}</Alert>
        )}

        {parsed && parsed.indicators.length > 0 && (
          <section aria-label="Document warning indicators" className="flex flex-col gap-3">
            <h2 className="text-sm font-semibold">Document warning indicators</h2>
            {parsed.indicators.map((ind, idx) => (
              <div key={`${ind.kind}-${idx}`} className="rounded border border-danger p-3 text-sm">
                <div className="flex flex-wrap items-baseline justify-between gap-2">
                  <span className="font-semibold text-danger">{ind.kind}</span>
                  <span className="text-xs text-muted">{formatEvidenceLocation(ind)}</span>
                </div>
                <p className="mt-1 whitespace-pre-wrap font-mono text-xs">{ind.excerpt}</p>
              </div>
            ))}
          </section>
        )}

        {parsed && (parsed.blocking.length > 0 || parsed.needs_acknowledgement.length > 0) && (
          <section aria-label="Review requirements" className="grid gap-4 sm:grid-cols-2">
            {(
              [
                ["Blocking", parsed.blocking],
                ["Needs acknowledgement", parsed.needs_acknowledgement],
              ] as const
            ).map(([title, list]) => (
              <div key={title}>
                <h2 className="mb-2 text-sm font-semibold">{title}</h2>
                {list.length === 0 ? (
                  <Empty>None.</Empty>
                ) : (
                  <ul className="list-disc pl-5 text-sm">
                    {list.map((b, idx) => (
                      <li key={`${b}-${idx}`}>{b}</li>
                    ))}
                  </ul>
                )}
              </div>
            ))}
          </section>
        )}

        {parsed && grouped.critical.length > 0 && (
          <section aria-label="Critical fields" className="flex flex-col gap-3">
            <h2 className="text-sm font-semibold">Critical fields</h2>
            {rows(grouped.critical)}
          </section>
        )}
        {parsed && grouped.other.length > 0 && (
          <section aria-label="Other fields" className="flex flex-col gap-3">
            <h2 className="text-sm font-semibold">Other fields</h2>
            {rows(grouped.other)}
          </section>
        )}

        {parsed && Object.keys(parsed.notes).length > 0 && (
          <section aria-label="AI notes" className="grid gap-4 sm:grid-cols-2">
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
          </section>
        )}

        {parsed && Object.keys(parsed.unresolved).length > 0 && (
          <section aria-label="Unresolved items" className="flex flex-col gap-2">
            <h2 className="text-sm font-semibold">Unresolved items</h2>
            <KeyValueGrid data={parsed.unresolved} />
          </section>
        )}

        {detail.validation && Object.keys(detail.validation).length > 0 && (
          <section aria-label="Validation summary" className="flex flex-col gap-2">
            <h2 className="text-sm font-semibold">Validation summary (at read time)</h2>
            <KeyValueGrid data={detail.validation} />
          </section>
        )}
      </div>
    </details>
  );
}
