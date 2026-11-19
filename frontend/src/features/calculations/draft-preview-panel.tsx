import { useMutation } from "@tanstack/react-query";
import { useState } from "react";

import { Alert, Button, Card, Empty, Field } from "../../components/ui";
import { api } from "../../lib/api/client";
import { ApiError, unwrap } from "../../lib/api/errors";
import { ExceptionCell, KeyValueGrid, TraceTree, ValidationDetailsAlert } from "./common";
import { RESULT_STATUS_LABELS, type DraftPreviewLine } from "./logic";
import { formatDate, formatMoney, formatQty } from "../../lib/format";

export function DraftPreviewPanel({ draftId }: { draftId: string }) {
  const [limitInput, setLimitInput] = useState("");
  const [limitError, setLimitError] = useState<string | null>(null);
  const [expandedLineId, setExpandedLineId] = useState<string | null>(null);

  const preview = useMutation({
    mutationFn: (limit: number | undefined) =>
      unwrap(
        api.POST("/api/v1/agreement-drafts/{draft_id}/preview-calculation", {
          params: { path: { draft_id: draftId } },
          body: limit === undefined ? undefined : { limit },
        }),
      ),
  });

  const err = preview.error as ApiError | null;

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    const trimmed = limitInput.trim();
    if (trimmed === "") {
      setLimitError(null);
      preview.mutate(undefined); // optional (API_SPECIFICATION): the server applies its own default
      return;
    }
    if (!/^-?\d+$/.test(trimmed)) {
      setLimitError("Enter a whole number.");
      return;
    }
    setLimitError(null);
    preview.mutate(Number.parseInt(trimmed, 10));
  }

  return (
    <Card title="Calculation preview">
      <div className="flex flex-col gap-4">
        <Alert tone="info">
          Preview — not saved. Runs against sample sales lines without persisting results.
        </Alert>

        <form className="flex flex-wrap items-end gap-3" onSubmit={handleSubmit}>
          <div className="w-40">
            <Field
              label="Sample limit"
              type="number"
              value={limitInput}
              error={limitError ?? undefined}
              onChange={(e) => {
                setLimitError(null);
                setLimitInput(e.target.value);
              }}
            />
          </div>
          <Button type="submit" variant="secondary" disabled={preview.isPending}>
            {preview.isPending ? "Running preview…" : "Run preview"}
          </Button>
        </form>

        <ValidationDetailsAlert error={err} />

        {preview.data && (
          <div className="flex flex-col gap-4">
            <dl className="grid gap-3 text-sm sm:grid-cols-4">
              <div>
                <dt className="text-muted">Status</dt>
                <dd className="font-medium">Preview — not saved</dd>
              </div>
              <div>
                <dt className="text-muted">Kind</dt>
                <dd className="font-medium">{preview.data.kind.toUpperCase()}</dd>
              </div>
              <div>
                <dt className="text-muted">Run date</dt>
                <dd className="font-medium">{formatDate(preview.data.run_date)}</dd>
              </div>
              <div>
                <dt className="text-muted">Sample size</dt>
                <dd className="font-medium">{preview.data.sample_size}</dd>
              </div>
            </dl>

            <div>
              <h2 className="mb-2 text-sm font-semibold">Totals</h2>
              <KeyValueGrid data={preview.data.totals} empty="No totals." />
            </div>

            {preview.data.accrual && (
              <div className="rounded-lg border border-line p-3">
                <h2 className="mb-2 text-sm font-semibold">Accrual preview</h2>
                <dl className="grid gap-3 text-sm sm:grid-cols-3">
                  <div>
                    <dt className="text-muted">Period</dt>
                    <dd>
                      {formatDate(preview.data.accrual.period_start)} –{" "}
                      {formatDate(preview.data.accrual.period_end)}
                    </dd>
                  </div>
                  <div>
                    <dt className="text-muted">Cumulative qualifying</dt>
                    <dd className="font-mono">{formatQty(preview.data.accrual.cumulative_qualifying)}</dd>
                  </div>
                  <div>
                    <dt className="text-muted">Cumulative payout base</dt>
                    <dd className="font-mono">{formatMoney(preview.data.accrual.cumulative_payout_base)}</dd>
                  </div>
                  <div>
                    <dt className="text-muted">Cumulative earned</dt>
                    <dd className="font-mono">{formatMoney(preview.data.accrual.cumulative_earned)}</dd>
                  </div>
                  <div>
                    <dt className="text-muted">Tier</dt>
                    <dd>
                      {preview.data.accrual.tier_number !== null
                        ? `Tier ${preview.data.accrual.tier_number} (${preview.data.accrual.tier_rate ?? "—"})`
                        : "None"}
                    </dd>
                  </div>
                  <div>
                    <dt className="text-muted">Flags</dt>
                    <dd>{preview.data.accrual.flags.length ? preview.data.accrual.flags.join(", ") : "—"}</dd>
                  </div>
                </dl>
                {preview.data.accrual.note && (
                  <p className="mt-2 text-xs text-muted">{preview.data.accrual.note}</p>
                )}
              </div>
            )}

            {preview.data.lines.length === 0 ? (
              <Empty>No sample lines matched.</Empty>
            ) : (
              <div className="overflow-x-auto">
                <table className="w-full text-left text-sm">
                  <caption className="sr-only">Preview lines</caption>
                  <thead className="text-muted">
                    <tr>
                      <th scope="col" className="py-2 pr-4 font-medium">
                        Document
                      </th>
                      <th scope="col" className="py-2 pr-4 font-medium">
                        Line
                      </th>
                      <th scope="col" className="py-2 pr-4 font-medium">
                        Invoice date
                      </th>
                      <th scope="col" className="py-2 pr-4 font-medium">
                        Status
                      </th>
                      <th scope="col" className="py-2 pr-4 font-medium">
                        Exception
                      </th>
                      <th scope="col" className="py-2 pr-4 text-right font-medium">
                        Claim amount
                      </th>
                      <th scope="col" className="py-2 pr-4 text-right font-medium">
                        Eligible qty
                      </th>
                      <th scope="col" className="py-2 pr-4 font-medium">
                        Trace
                      </th>
                    </tr>
                  </thead>
                  <tbody>
                    {preview.data.lines.map((line: DraftPreviewLine) => {
                      const isExpanded = expandedLineId === line.line_id;
                      return (
                        <tr key={line.line_id} className="border-t border-line align-top">
                          <td className="py-2 pr-4">{line.document_number}</td>
                          <td className="py-2 pr-4">{line.line_number}</td>
                          <td className="py-2 pr-4">{formatDate(line.invoice_date)}</td>
                          <td className="py-2 pr-4">{RESULT_STATUS_LABELS[line.status] ?? line.status}</td>
                          <td className="py-2 pr-4">
                            <ExceptionCell code={line.exception_code} />
                          </td>
                          <td className="py-2 pr-4 text-right font-mono">{formatMoney(line.claim_amount)}</td>
                          <td className="py-2 pr-4 text-right font-mono">
                            {formatQty(line.eligible_quantity)}
                          </td>
                          <td className="py-2 pr-4">
                            <Button
                              variant="secondary"
                              aria-expanded={isExpanded}
                              aria-label={`Trace for ${line.document_number} line ${line.line_number}`}
                              onClick={() =>
                                setExpandedLineId((cur) => (cur === line.line_id ? null : line.line_id))
                              }
                            >
                              {isExpanded ? "Hide" : "Trace"}
                            </Button>
                            {isExpanded && (
                              <div className="mt-2 min-w-64">
                                <TraceTree trace={line.trace} label={`Trace for ${line.document_number}`} />
                              </div>
                            )}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        )}
      </div>
    </Card>
  );
}
