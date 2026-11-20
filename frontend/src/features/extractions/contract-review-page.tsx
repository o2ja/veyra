import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";

import { Alert, Button, Card, ErrorAlert, Loading, PermissionDenied } from "../../components/ui";
import { api } from "../../lib/api/client";
import { ApiError, unwrap } from "../../lib/api/errors";
import { formatDate } from "../../lib/format";
import { useSession } from "../../lib/session";
import { setAgreementType, type DraftOut, type Payload } from "../agreements/logic";
import { ExtractionsNav } from "./common";
import { parseExtractionResult, readConfirmed, writeConfirmed } from "./logic";
import {
  buildReview,
  editText,
  getAt,
  isPending,
  matchSentence,
  parseEdit,
  pendingCount,
  previewSentence,
  setAt,
  type ReviewItem,
} from "./review";
import { TechnicalDetails } from "./technical-details";

const TYPE_LABELS: Record<string, string> = { spa: "SPA (ship and debit)", rebate: "Volume rebate" };
const CONTROL = "rounded-md border border-line bg-panel px-3 py-2 text-sm";

function Quote({ item, onOpen }: { item: ReviewItem; onOpen: (page: number) => void }) {
  if (!item.quote) return null;
  return (
    <blockquote className="mt-2 border-l-2 border-line pl-3 text-sm text-muted">
      “{item.quote}”
      {item.page !== null && (
        <button
          type="button"
          className="ml-2 whitespace-nowrap text-brand underline"
          onClick={() => onOpen(item.page as number)}
        >
          p.{item.page} ↗
        </button>
      )}
    </blockquote>
  );
}

function ItemRow({
  item,
  editable,
  text,
  saving,
  done,
  onText,
  onSave,
  onConfirm,
  onOpen,
  editorHref,
}: {
  item: ReviewItem;
  editable: boolean;
  text: string | undefined;
  saving: boolean;
  done: boolean;
  onText: (value: string | undefined) => void;
  onSave: (value: unknown) => void;
  onConfirm: () => void;
  onOpen: (page: number) => void;
  editorHref: string;
}) {
  const shown = text ?? (item.path ? editText(item.path, item.value) : "");
  const parsed = item.path ? parseEdit(item.path, shown) : undefined;
  const changed = text !== undefined && text !== editText(item.path ?? "", item.value);
  const input = item.input;
  const canEdit = editable && item.path !== null && input !== null;
  const confirmable =
    !done && !item.blocking && (item.optional || item.path === null || item.display !== "—");

  return (
    <li className="rounded-lg border border-line p-4" data-testid="review-item">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <span className="font-medium">{item.label}</span>
        {item.needs && (
          <span className="text-xs font-medium">{done ? "✔ Confirmed" : "⚠ Needs your decision"}</span>
        )}
      </div>
      {item.why && <p className="mt-1 text-sm text-muted">{item.why}</p>}
      <Quote item={item} onOpen={onOpen} />

      {canEdit && input ? (
        <div className="mt-3 flex flex-wrap items-center gap-2">
          {input.kind === "select" ? (
            <select
              aria-label={item.label}
              className={CONTROL}
              value={shown}
              onChange={(e) => onText(e.target.value)}
            >
              {shown === "" && (
                <option value="" disabled>
                  Choose…
                </option>
              )}
              {input.options.map(([v, l]) => (
                <option key={v} value={v}>
                  {l}
                </option>
              ))}
            </select>
          ) : (
            <input
              aria-label={item.label}
              className={`${CONTROL} w-56`}
              type={input.kind === "date" ? "date" : "text"}
              inputMode={input.kind === "number" ? "numeric" : undefined}
              placeholder={input.kind === "percent" ? "e.g. 5" : undefined}
              value={shown}
              onChange={(e) => onText(e.target.value)}
            />
          )}
          {input.kind === "percent" && <span className="text-sm text-muted">%</span>}
          <Button
            disabled={saving || !changed || parsed === undefined}
            onClick={() => parsed !== undefined && onSave(parsed)}
          >
            Save
          </Button>
          {changed && (
            <Button variant="secondary" disabled={saving} onClick={() => onText(undefined)}>
              Discard
            </Button>
          )}
          {item.needs && !changed && confirmable && (
            <Button variant="secondary" disabled={saving} onClick={onConfirm}>
              Looks right
            </Button>
          )}
        </div>
      ) : (
        <div className="mt-3 flex flex-wrap items-center gap-3 text-sm">
          {(item.display !== "—" || item.path === null) && (
            <span>
              {item.path !== null && "Currently: "}
              {item.display}
            </span>
          )}
          {editable && item.needs && confirmable && (
            <Button variant="secondary" disabled={saving} onClick={onConfirm}>
              Looks right
            </Button>
          )}
          {editable && item.blocking && input === null && (
            <Link to={editorHref} className="text-brand underline">
              Fix in the full editor
            </Link>
          )}
        </div>
      )}
    </li>
  );
}

export function ContractReviewPage() {
  const { extractionId = "" } = useParams();
  const { can } = useSession();
  const client = useQueryClient();
  const [answered, setAnswered] = useState<ReadonlySet<string>>(new Set());
  const [edits, setEdits] = useState<ReadonlyMap<string, string>>(new Map());
  const [submitted, setSubmitted] = useState(false);

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
  const draftId = detail.data?.draft_id ?? null;
  const fileId = detail.data?.file_id ?? null;

  const draft = useQuery({
    queryKey: ["agreement-drafts", draftId],
    enabled: draftId !== null,
    queryFn: () =>
      unwrap(
        api.GET("/api/v1/agreement-drafts/{draft_id}", { params: { path: { draft_id: draftId ?? "" } } }),
      ),
  });
  const file = useQuery({
    queryKey: ["files", fileId],
    enabled: fileId !== null,
    queryFn: () =>
      unwrap(api.GET("/api/v1/files/{file_id}", { params: { path: { file_id: fileId ?? "" } } })),
  });

  const parsed = parseExtractionResult(detail.data?.result);
  const d = draft.data;
  const updatedAt = d?.updated_at;
  // "Looks right" answers survive a reload while the draft is unchanged since they were given
  const stored = useMemo(
    () => (updatedAt ? readConfirmed(extractionId, updatedAt) : []),
    [extractionId, updatedAt],
  );
  const confirmed = useMemo(() => new Set([...stored, ...answered]), [stored, answered]);
  const remember = (next: ReadonlySet<string>, at: string) => {
    setAnswered(next);
    writeConfirmed(extractionId, at, next);
  };
  const review = useMemo(
    () => (parsed && d ? buildReview(parsed, d.payload, d.validation) : null),
    [parsed, d],
  );
  const editable = d?.status === "draft" && can("agreement.draft");
  const valid = d?.validation?.valid === true;

  const preview = useQuery({
    queryKey: ["draft-preview", draftId, d?.revision],
    enabled: valid && can("calculation.run"),
    retry: false,
    queryFn: () =>
      unwrap(
        api.POST("/api/v1/agreement-drafts/{draft_id}/preview-calculation", {
          params: { path: { draft_id: draftId ?? "" } },
          body: { limit: 200 },
        }),
      ),
  });

  const keep = (fresh: DraftOut) => client.setQueryData(["agreement-drafts", fresh.id], fresh);

  const validate = useMutation({
    mutationFn: () =>
      unwrap(
        api.POST("/api/v1/agreement-drafts/{draft_id}/validate", {
          params: { path: { draft_id: draftId ?? "" } },
        }),
      ),
    onSuccess: keep,
  });
  // a draft that was never checked (no validation yet) is checked once, so the list below is complete
  useEffect(() => {
    if (d && d.status === "draft" && d.validation === null && can("agreement.draft") && validate.isIdle) {
      validate.mutate();
    }
  }, [d, can, validate]);

  const save = useMutation({
    mutationFn: async (v: { item: ReviewItem; value: unknown }) => {
      if (!d || !v.item.path) throw new Error("Nothing to save");
      const payload =
        v.item.path === "header.agreement_type" && (v.value === "spa" || v.value === "rebate")
          ? (setAgreementType(d.payload as unknown as Payload, v.value) as unknown as Record<string, unknown>)
          : setAt(d.payload, v.item.path, v.value);
      await unwrap(
        api.PUT("/api/v1/agreement-drafts/{draft_id}", {
          params: { path: { draft_id: d.id }, header: { "if-match": `"${d.revision}"` } },
          body: {
            payload,
            change_summary: `Contract review: ${v.item.label}`.slice(0, 200),
          },
        }),
      );
      return unwrap(
        api.POST("/api/v1/agreement-drafts/{draft_id}/validate", { params: { path: { draft_id: d.id } } }),
      );
    },
    onSuccess: (fresh, v) => {
      keep(fresh);
      // re-read from the server so the counts, checks, estimate and submit state match the database
      void client.invalidateQueries({ queryKey: ["agreement-drafts", fresh.id] });
      void client.invalidateQueries({ queryKey: ["document-extractions"] });
      void client.invalidateQueries({ queryKey: ["draft-preview"] });
      remember(new Set(confirmed).add(v.item.id), fresh.updated_at);
      setEdits((m) => {
        const next = new Map(m);
        next.delete(v.item.id);
        return next;
      });
    },
    onError: () => void draft.refetch(),
  });

  const submit = useMutation({
    mutationFn: () =>
      unwrap(
        api.POST("/api/v1/agreement-drafts/{draft_id}/submit", {
          params: { path: { draft_id: draftId ?? "" } },
          body: {
            acknowledged_warnings: (d?.validation?.warnings ?? []).flatMap((w) => (w.id ? [w.id] : [])),
          },
        }),
      ),
    onSuccess: (fresh) => {
      keep(fresh);
      setSubmitted(true);
      void client.invalidateQueries({ queryKey: ["agreement-drafts"] });
      void client.invalidateQueries({ queryKey: ["document-extractions"] });
    },
  });

  const openDocument = useMutation({
    mutationFn: async (page?: number) => {
      if (!fileId) return;
      const res = await unwrap(
        api.GET("/api/v1/files/{file_id}/download-url", { params: { path: { file_id: fileId } } }),
      );
      window.open(page === undefined ? res.url : `${res.url}#page=${page}`, "_blank", "noopener");
    },
  });

  const err = detail.error as ApiError | null;
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
          <Alert>This contract read could not be found.</Alert>
          <Link to="/extractions" className="mt-4 inline-block text-brand underline">
            Back to extractions
          </Link>
        </Card>
      </>
    );
  }

  const ex = detail.data;
  const reading = ex?.status === "queued" || ex?.status === "processing";
  const editorHref = `/agreement-drafts/${draftId ?? ""}?editor=1`;
  const pending = review ? pendingCount(review, confirmed) : 0;
  const blockers: string[] = [];
  if (pending > 0)
    blockers.push(`${pending} ${pending === 1 ? "item still needs" : "items still need"} your decision`);
  if (edits.size > 0) blockers.push("Save or discard the changes you typed");
  if (review && pending === 0 && !valid) blockers.push("The agreement checks still report a problem");
  const canSubmit = editable && !!review && blockers.length === 0;

  const header = d ? (d.payload as { header?: Record<string, unknown> }).header : undefined;
  const printedMaker = parsed?.fields.find((f) => f.key === "header.manufacturer_name")?.value;
  const maker = typeof printedMaker === "string" ? printedMaker : String(header?.manufacturer_code ?? "—");
  const tiers = d ? getAt(d.payload, "tiers") : undefined;
  const type = String(header?.agreement_type ?? parsed?.agreement_type ?? "");
  const match = review ? matchSentence(review) : null;

  const rowProps = (item: ReviewItem) => ({
    item,
    editable: editable === true,
    text: edits.get(item.id),
    saving: save.isPending,
    done: !isPending(item, confirmed),
    onText: (value: string | undefined) =>
      setEdits((m) => {
        const next = new Map(m);
        if (value === undefined) next.delete(item.id);
        else next.set(item.id, value);
        return next;
      }),
    onSave: (value: unknown) => save.mutate({ item, value }),
    onConfirm: () => d && remember(new Set(confirmed).add(item.id), d.updated_at),
    onOpen: (page: number) => openDocument.mutate(page),
    editorHref,
  });

  return (
    <>
      <ExtractionsNav />
      <div className="flex flex-col gap-6">
        <Card title="Contract review">
          <ErrorAlert error={err ?? (draft.error as ApiError | null)} />
          <ErrorAlert error={(save.error ?? submit.error ?? openDocument.error) as ApiError | null} />
          {detail.isPending || reading ? (
            <Loading label={reading ? "Reading the contract…" : "Loading…"} />
          ) : ex?.status === "failed" ? (
            <Alert>
              The contract could not be read{ex.error_detail ? `: ${ex.error_detail}` : "."} You can enter the
              agreement by hand instead.
            </Alert>
          ) : d && d.status === "needs_manual_entry" ? (
            <div className="flex flex-col gap-3">
              <Alert tone="info">
                Nothing in this document could be confirmed automatically. Enter the agreement by hand.
              </Alert>
              <Link to={`/agreement-drafts/${d.id}`} className="text-brand underline">
                Open the agreement editor
              </Link>
            </div>
          ) : review && d ? (
            <div className="flex flex-col gap-4">
              <div>
                <p className="text-lg font-medium">
                  {file.data?.original_name ?? "Contract"}
                  {parsed?.page_count
                    ? ` · ${parsed.page_count} ${parsed.page_count === 1 ? "page" : "pages"}`
                    : ""}
                </p>
                <p className="mt-1 text-sm text-muted">
                  {[
                    maker,
                    TYPE_LABELS[type] ?? type,
                    header?.valid_from || header?.valid_to
                      ? `${formatDate(String(header?.valid_from ?? ""))} – ${formatDate(String(header?.valid_to ?? ""))}`
                      : null,
                    review.customers.total > 0 ? `${review.customers.total} customers` : null,
                    review.skus.total > 0 ? `${review.skus.total} SKUs` : null,
                    Array.isArray(tiers) && tiers.length > 0 ? `${tiers.length} tiers` : null,
                  ]
                    .filter(Boolean)
                    .join(" · ")}
                </p>
              </div>

              <ul className="flex flex-col gap-1 text-sm">
                <li>✔ {review.found.filter((i) => i.quote).length} fields found with a quote</li>
                <li>⚠ {pending} need your decision</li>
                {review.conflicts > 0 && <li>✖ {review.conflicts} conflicts with existing agreements</li>}
                {match && <li>{match}</li>}
              </ul>

              {valid && can("calculation.run") && (
                <p className="text-sm">
                  {preview.isPending ? (
                    "Estimating from your imported data…"
                  ) : preview.data ? (
                    <>
                      <strong>{previewSentence(preview.data)}</strong>
                      <span className="text-muted"> (preview only, nothing is saved)</span>
                    </>
                  ) : null}
                </p>
              )}
              {!valid && editable && (
                <p className="text-sm text-muted">The estimate appears once the open items are resolved.</p>
              )}

              {d.status === "pending_approval" || submitted ? (
                <Alert tone="ok">
                  Submitted for approval. An approver will review it.{" "}
                  <Link to={`/agreement-drafts/${d.id}`} className="underline">
                    View the draft
                  </Link>
                </Alert>
              ) : d.status !== "draft" ? (
                <Alert tone="info">
                  This agreement is {d.status.replaceAll("_", " ")}.{" "}
                  <Link to={`/agreement-drafts/${d.id}`} className="underline">
                    Open the draft
                  </Link>
                </Alert>
              ) : (
                <div className="flex flex-col gap-2">
                  <div className="flex flex-wrap items-center gap-3">
                    {pending > 0 && (
                      <Button
                        variant="secondary"
                        onClick={() =>
                          document.getElementById("needs-decision")?.scrollIntoView({ behavior: "smooth" })
                        }
                      >
                        Review the {pending} {pending === 1 ? "item" : "items"} ↓
                      </Button>
                    )}
                    {editable && (
                      <Button disabled={!canSubmit || submit.isPending} onClick={() => submit.mutate()}>
                        {submit.isPending ? "Submitting…" : "Submit for approval"}
                      </Button>
                    )}
                    <Link to={editorHref} className="text-sm text-brand underline">
                      Open the full editor
                    </Link>
                  </div>
                  {editable && blockers.length > 0 && (
                    <p className="text-xs text-muted">{blockers.join(". ")}.</p>
                  )}
                  {!editable && <Alert tone="info">You can review this contract but not change it.</Alert>}
                </div>
              )}
            </div>
          ) : (
            <Loading />
          )}
        </Card>

        {parsed && parsed.indicators.length > 0 && (
          <Alert>
            Part of this document reads like instructions to the AI. It was not followed. Check the quoted
            pages yourself.
          </Alert>
        )}

        {review && d && review.needs.length > 0 && (
          <Card title="Needs your decision">
            <ul id="needs-decision" className="flex flex-col gap-3">
              {review.needs.map((i) => (
                <ItemRow key={i.id} {...rowProps(i)} />
              ))}
            </ul>
          </Card>
        )}

        {review && d && (
          <details className="rounded-xl border border-line bg-panel p-5 shadow-card sm:p-6">
            <summary className="cursor-pointer text-lg font-semibold tracking-tight">
              Found in the document ({review.found.length})
            </summary>
            <ul className="mt-4 flex flex-col gap-3">
              {review.found.map((i) => (
                <ItemRow key={i.id} {...rowProps(i)} />
              ))}
            </ul>
          </details>
        )}

        {ex && (
          <TechnicalDetails
            detail={ex}
            parsed={parsed}
            openingDocument={openDocument.isPending}
            onOpenDocument={(p) => openDocument.mutate(p ?? undefined)}
          />
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
