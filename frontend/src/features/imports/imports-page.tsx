import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router-dom";

import type { paths } from "../../api/schema";
import { Card, PermissionDenied } from "../../components/ui";
import { api } from "../../lib/api/client";
import { ApiError, unwrap } from "../../lib/api/errors";
import { useSession } from "../../lib/session";
import { DataTable, PagerControls, SelectField, usePager, utc } from "../masterdata/common";
import { STATUS_TEXT, TYPE_TEXT, isBusy } from "./logic";

type Query = NonNullable<paths["/api/v1/imports"]["get"]["parameters"]["query"]>;

/** Import history (all roles): status and counts per batch. */
export function ImportsPage() {
  const { can } = useSession();
  const pager = usePager();
  const [status, setStatus] = useState<NonNullable<Query["status"]> | "">("");
  const [type, setType] = useState<NonNullable<Query["import_type"]> | "">("");
  const list = useQuery({
    queryKey: ["imports", status, type, pager.cursor],
    queryFn: () =>
      unwrap(
        api.GET("/api/v1/imports", {
          params: {
            query: { status: status || null, import_type: type || null, cursor: pager.cursor, limit: 50 },
          },
        }),
      ),
    refetchInterval: (q) => (q.state.data?.data.some(isBusy) ? 2000 : false),
  });
  if ((list.error as ApiError)?.status === 403) return <PermissionDenied />;

  return (
    <Card
      title="Imports"
      actions={
        can("import.run") && (
          <Link to="/imports/new" className="rounded-md bg-brand px-3 py-2 text-sm font-medium text-white">
            New import
          </Link>
        )
      }
    >
      <div className="mb-4 grid max-w-xl gap-3 sm:grid-cols-2">
        <SelectField
          label="Status"
          value={status}
          onChange={(e) => {
            setStatus(e.target.value as typeof status);
            pager.reset();
          }}
        >
          <option value="">Any status</option>
          {Object.entries(STATUS_TEXT).map(([v, t]) => (
            <option key={v} value={v}>
              {t}
            </option>
          ))}
        </SelectField>
        <SelectField
          label="Type"
          value={type}
          onChange={(e) => {
            setType(e.target.value as typeof type);
            pager.reset();
          }}
        >
          <option value="">Any type</option>
          {Object.entries(TYPE_TEXT).map(([v, t]) => (
            <option key={v} value={v}>
              {t}
            </option>
          ))}
        </SelectField>
      </div>
      <DataTable
        caption="Import history"
        loading={list.isPending}
        error={list.error}
        rows={list.data?.data}
        empty="No imports yet."
        columns={[
          {
            header: "Started (UTC)",
            cell: (i) => (
              <Link to={`/imports/${i.id}`} className="text-brand underline">
                {utc(i.created_at)}
              </Link>
            ),
          },
          { header: "Type", cell: (i) => TYPE_TEXT[i.import_type] ?? i.import_type },
          { header: "Source", cell: (i) => i.source_system },
          { header: "Status", cell: (i) => STATUS_TEXT[i.status] ?? i.status },
          { header: "Rows", cell: (i) => i.counts.rows_total },
          { header: "Inserted", cell: (i) => i.counts.inserted },
          { header: "Changed", cell: (i) => i.counts.changed },
          { header: "Unchanged", cell: (i) => i.counts.unchanged },
          { header: "Rejected", cell: (i) => i.counts.rejected },
        ]}
      />
      <PagerControls pager={pager} page={list.data?.page} />
    </Card>
  );
}
