import { useMutation, useQuery } from "@tanstack/react-query";
import { Link, useNavigate, useSearchParams } from "react-router-dom";

import { Button, Card, ErrorAlert } from "../../components/ui";
import { api } from "../../lib/api/client";
import { ApiError, unwrap } from "../../lib/api/errors";
import { useSession } from "../../lib/session";
import { DataTable, PagerControls, SelectField, usePager, utc } from "../masterdata/common";
import { AgreementsNav } from "./common";
import { DRAFT_STATUSES, DRAFT_STATUS_LABELS, emptyPayload } from "./logic";

type DraftStatus = (typeof DRAFT_STATUSES)[number];
type FilterStatus = DraftStatus | "all";

function isDraftStatus(v: string | null): v is DraftStatus {
  return Boolean(v && (DRAFT_STATUSES as readonly string[]).includes(v));
}

export function DraftsPage() {
  const { can } = useSession();
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const pager = usePager();

  const defaultStatus: DraftStatus = can("agreement.approve") ? "pending_approval" : "draft";
  const paramStatus = searchParams.get("status");
  const statusFilter: FilterStatus =
    paramStatus === "all" ? "all" : isDraftStatus(paramStatus) ? paramStatus : defaultStatus;
  const agreementIdParam = searchParams.get("agreement_id");

  const list = useQuery({
    queryKey: ["agreement-drafts", statusFilter, agreementIdParam, pager.cursor],
    queryFn: () =>
      unwrap(
        api.GET("/api/v1/agreement-drafts", {
          params: {
            query: {
              status: statusFilter === "all" ? null : [statusFilter],
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
            <option value="all">All statuses</option>
            {DRAFT_STATUSES.map((st) => (
              <option key={st} value={st}>
                {DRAFT_STATUS_LABELS[st] ?? st}
              </option>
            ))}
          </SelectField>
        </div>

        <ErrorAlert error={createDraft.error as ApiError | null} />
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
              cell: (d) => DRAFT_STATUS_LABELS[d.status] ?? d.status,
            },
            { header: "Source", cell: (d) => d.source },
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
            { header: "Updated", cell: (d) => utc(d.updated_at) },
          ]}
        />
        <PagerControls pager={pager} page={list.data?.page} />
      </Card>
    </>
  );
}
