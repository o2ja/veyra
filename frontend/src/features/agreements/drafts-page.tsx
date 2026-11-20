import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";

import { Badge, Button, Card, ErrorAlert, PermissionDenied } from "../../components/ui";
import { api } from "../../lib/api/client";
import { ApiError, unwrap } from "../../lib/api/errors";
import { useSession } from "../../lib/session";
import { DataTable, PagerControls, SelectField, usePager } from "../masterdata/common";
import { ConfirmDialog } from "../claims/common";
import { AgreementsNav } from "./common";
import { DRAFT_STATUSES, DRAFT_STATUS_LABELS, emptyPayload } from "./logic";
import { formatDateTime } from "../../lib/format";

type DraftStatus = (typeof DRAFT_STATUSES)[number];
type FilterStatus = DraftStatus | "all" | "open";

const OPEN_STATUSES: DraftStatus[] = ["draft", "needs_manual_entry", "pending_approval"];
// An unsubmitted draft can be deleted (the server soft-deletes it to `cancelled`).
const DELETABLE: string[] = ["draft", "needs_manual_entry"];
const SOURCE_LABELS: Record<string, string> = {
  manual: "Typed in",
  spreadsheet_template: "Spreadsheet",
  ai_extraction: "Read by AI",
};

function isDraftStatus(v: string | null): v is DraftStatus {
  return Boolean(v && (DRAFT_STATUSES as readonly string[]).includes(v));
}

export function DraftsPage() {
  const { can } = useSession();
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const pager = usePager();
  const client = useQueryClient();
  const [deleting, setDeleting] = useState<string | null>(null);

  // Default to every open draft so an AI-generated draft is never hidden behind a role-based filter.
  const paramStatus = searchParams.get("status");
  const statusFilter: FilterStatus =
    paramStatus === "all" ? "all" : isDraftStatus(paramStatus) ? paramStatus : "open";
  const agreementIdParam = searchParams.get("agreement_id");

  const list = useQuery({
    queryKey: ["agreement-drafts", statusFilter, agreementIdParam, pager.cursor],
    queryFn: () =>
      unwrap(
        api.GET("/api/v1/agreement-drafts", {
          params: {
            query: {
              status:
                statusFilter === "all" ? null : statusFilter === "open" ? OPEN_STATUSES : [statusFilter],
              agreement_id: agreementIdParam || null,
              limit: 50,
              cursor: pager.cursor,
            },
          },
        }),
      ),
  });

  const createDraft = useMutation({
    mutationFn: () =>
      unwrap(
        api.POST("/api/v1/agreement-drafts", {
          // the form starts from these visible defaults, so the server holds exactly what the user sees
          body: { source: "manual", payload: emptyPayload() as unknown as Record<string, unknown> },
        }),
      ),
    onSuccess: (draft) => {
      navigate(`/agreement-drafts/${draft.id}`);
    },
  });

  const remove = useMutation({
    mutationFn: (id: string) =>
      unwrap(api.DELETE("/api/v1/agreement-drafts/{draft_id}", { params: { path: { draft_id: id } } })),
    onSuccess: () => {
      setDeleting(null);
      void client.invalidateQueries({ queryKey: ["agreement-drafts"] });
    },
  });

  if ((list.error as ApiError)?.status === 403) {
    return (
      <>
        <AgreementsNav />
        <PermissionDenied />
      </>
    );
  }

  return (
    <>
      <AgreementsNav />
      <Card
        title="Agreement drafts"
        actions={
          can("agreement.draft") ? (
            <Button disabled={createDraft.isPending} onClick={() => createDraft.mutate()}>
              New draft
            </Button>
          ) : undefined
        }
      >
        <div className="mb-4 flex flex-wrap items-end gap-4">
          <SelectField
            label="Status"
            value={statusFilter}
            onChange={(e) => {
              const next = new URLSearchParams(searchParams);
              next.set("status", e.target.value);
              setSearchParams(next);
              pager.reset();
            }}
          >
            <option value="open">All open (to review + pending approval)</option>
            <option value="all">All statuses</option>
            {DRAFT_STATUSES.map((st) => (
              <option key={st} value={st}>
                {DRAFT_STATUS_LABELS[st] ?? st}
              </option>
            ))}
          </SelectField>
        </div>

        <ErrorAlert error={(createDraft.error ?? remove.error) as ApiError | null} />
        <DataTable
          caption="Agreement drafts"
          loading={list.isPending}
          error={list.error}
          rows={list.data?.data}
          empty="No matching drafts."
          columns={[
            {
              header: "Draft",
              cell: (d) => (
                <Link to={`/agreement-drafts/${d.id}`} className="text-brand underline">
                  {d.id.slice(0, 8)}
                </Link>
              ),
            },
            {
              header: "Status",
              cell: (d) => (
                <span className="flex flex-wrap items-center gap-1.5">
                  {DRAFT_STATUS_LABELS[d.status] ?? d.status}
                  {d.source === "ai_extraction" && DELETABLE.includes(d.status) && (
                    <Badge tone="warn">Needs review</Badge>
                  )}
                </span>
              ),
            },
            { header: "Source", cell: (d) => SOURCE_LABELS[d.source] ?? d.source },
            {
              header: "Agreement",
              cell: (d) =>
                d.agreement_id ? (
                  <Link to={`/agreements/${d.agreement_id}`} className="text-brand underline">
                    {d.agreement_id.slice(0, 8)}
                  </Link>
                ) : (
                  "New agreement"
                ),
            },
            { header: "Revision", cell: (d) => `r${d.revision}` },
            { header: "Updated", cell: (d) => formatDateTime(d.updated_at) },
            {
              header: "Actions",
              cell: (d) =>
                can("agreement.draft") && DELETABLE.includes(d.status) ? (
                  <Button
                    variant="secondary"
                    className="px-2.5 py-1 text-xs"
                    aria-label={`Delete draft ${d.id.slice(0, 8)}`}
                    onClick={() => setDeleting(d.id)}
                  >
                    Delete
                  </Button>
                ) : null,
            },
          ]}
        />
        <ConfirmDialog
          open={deleting !== null}
          onOpenChange={(open) => !open && setDeleting(null)}
          title="Delete this draft?"
          description="The draft is removed from the list. It was never submitted, so no agreement changes. This is recorded in the audit log."
          confirmLabel="Delete draft"
          confirmVariant="danger"
          busy={remove.isPending}
          onConfirm={() => deleting && remove.mutate(deleting)}
        />
        <PagerControls pager={pager} page={list.data?.page} />
      </Card>
    </>
  );
}
