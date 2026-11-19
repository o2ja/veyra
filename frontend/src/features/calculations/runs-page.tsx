import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useSearchParams } from "react-router-dom";

import { Alert, Button, Card, ErrorAlert, Field, PermissionDenied, fieldError } from "../../components/ui";
import { api } from "../../lib/api/client";
import { ApiError, unwrap } from "../../lib/api/errors";
import { useSession } from "../../lib/session";
import {
  Checkbox,
  DataTable,
  FormGrid,
  PagerControls,
  SelectField,
  useManufacturers,
  usePager,
} from "../masterdata/common";
import { CalculationsNav } from "./common";
import { RUN_STATUSES, RUN_STATUS_LABELS, flattenKeyValues, isPlainObject, type RunOut } from "./logic";
import { formatDate, formatDateTime } from "../../lib/format";

function formatRunError(err: Record<string, unknown> | null | undefined): string {
  if (!err || !isPlainObject(err)) return "—";
  const code = typeof err.code === "string" ? err.code : null;
  const message = typeof err.message === "string" ? err.message : null;
  if (code && message) return `${code}: ${message}`;
  if (message) return message;
  if (code) return code;
  return JSON.stringify(err);
}

export function RunsPage() {
  const { can } = useSession();
  const client = useQueryClient();
  const pager = usePager();
  const [searchParams, setSearchParams] = useSearchParams();
  const statusParam = searchParams.get("status") ?? "";
  const statusFilter = RUN_STATUSES.includes(statusParam as (typeof RUN_STATUSES)[number])
    ? (statusParam as (typeof RUN_STATUSES)[number])
    : "";

  const manufacturers = useManufacturers();
  const [dateFrom, setDateFrom] = useState("");
  const [dateTo, setDateTo] = useState("");
  const [asOf, setAsOf] = useState("");
  const [manufacturerIds, setManufacturerIds] = useState<string[]>([]);
  const [idemKey, setIdemKey] = useState<string>(() => crypto.randomUUID());
  const [activeRunId, setActiveRunId] = useState<string | null>(null);

  const list = useQuery({
    queryKey: ["calculation-runs", statusFilter, pager.cursor],
    queryFn: () =>
      unwrap(
        api.GET("/api/v1/calculation-runs", {
          params: {
            query: {
              status: statusFilter || null,
              limit: 50,
              cursor: pager.cursor,
            },
          },
        }),
      ),
    refetchInterval: (query) => {
      const rows = query.state.data?.data;
      if (!rows) return false;
      return rows.some((r) => r.status === "queued" || r.status === "running") ? 2000 : false;
    },
  });

  const polledRun = useQuery({
    queryKey: ["calculation-run-poll", activeRunId],
    enabled: Boolean(activeRunId),
    queryFn: async () => {
      const run = await unwrap(
        api.GET("/api/v1/calculation-runs/{run_id}", {
          params: { path: { run_id: activeRunId ?? "" } },
        }),
      );
      if (run.status !== "queued" && run.status !== "running") {
        void client.invalidateQueries({ queryKey: ["calculation-runs"] });
      }
      return run;
    },
    refetchInterval: (query) => {
      const st = query.state.data?.status;
      if (st === "queued" || st === "running") return 2000;
      return false;
    },
  });

  const createRun = useMutation({
    mutationFn: () =>
      unwrap(
        api.POST("/api/v1/calculation-runs", {
          params: { header: { "idempotency-key": idemKey } },
          body: {
            date_from: dateFrom,
            date_to: dateTo,
            as_of: asOf.trim() ? asOf.trim() : null,
            manufacturer_ids: manufacturerIds,
          },
        }),
      ),
    onSuccess: (accepted) => {
      setIdemKey(crypto.randomUUID());
      setActiveRunId(accepted.run.id);
      void client.invalidateQueries({ queryKey: ["calculation-runs"] });
    },
  });

  const listErr = list.error as ApiError | null;
  const createErr = createRun.error as ApiError | null;

  if (listErr?.status === 403 || listErr?.code === "forbidden") {
    return (
      <>
        <CalculationsNav />
        <PermissionDenied />
      </>
    );
  }

  function resetFormKey() {
    setIdemKey(crypto.randomUUID());
  }

  function toggleManufacturer(id: string, checked: boolean) {
    resetFormKey();
    setManufacturerIds((prev) => (checked ? [...prev, id] : prev.filter((x) => x !== id)));
  }

  return (
    <>
      <CalculationsNav />
      <div className="flex flex-col gap-6">
        {can("calculation.run") && (
          <Card title="New calculation run">
            <form
              className="flex flex-col gap-4"
              onSubmit={(e) => {
                e.preventDefault();
                createRun.mutate();
              }}
            >
              <FormGrid>
                <Field
                  label="Date from"
                  type="date"
                  required
                  value={dateFrom}
                  error={fieldError(createErr, "date_from")}
                  onChange={(e) => {
                    resetFormKey();
                    setDateFrom(e.target.value);
                  }}
                />
                <Field
                  label="Date to"
                  type="date"
                  required
                  value={dateTo}
                  error={fieldError(createErr, "date_to")}
                  onChange={(e) => {
                    resetFormKey();
                    setDateTo(e.target.value);
                  }}
                />
                <Field
                  label="As of (optional)"
                  type="date"
                  value={asOf}
                  error={fieldError(createErr, "as_of")}
                  onChange={(e) => {
                    resetFormKey();
                    setAsOf(e.target.value);
                  }}
                />
              </FormGrid>

              {(manufacturers.data?.data.length ?? 0) > 0 && (
                <fieldset className="flex flex-col gap-2">
                  <legend className="text-sm font-medium">
                    Manufacturers (leave all unchecked for all manufacturers)
                  </legend>
                  <div className="flex flex-wrap gap-4">
                    {manufacturers.data?.data.map((m) => (
                      <Checkbox
                        key={m.id}
                        label={`${m.name} (${m.code})`}
                        checked={manufacturerIds.includes(m.id)}
                        onChange={(checked) => toggleManufacturer(m.id, checked)}
                      />
                    ))}
                  </div>
                </fieldset>
              )}

              <ErrorAlert error={createErr} />
              {polledRun.data && (
                <Alert tone="info">
                  Run{" "}
                  <Link to={`/calculations/${polledRun.data.id}`} className="underline">
                    {polledRun.data.id.slice(0, 8)}
                  </Link>{" "}
                  is {RUN_STATUS_LABELS[polledRun.data.status] ?? polledRun.data.status}.
                </Alert>
              )}

              <div>
                <Button type="submit" disabled={createRun.isPending}>
                  {createRun.isPending ? "Starting…" : "Start calculation run"}
                </Button>
              </div>
            </form>
          </Card>
        )}

        <Card title="Calculation runs">
          <div className="mb-4 max-w-xs">
            <SelectField
              label="Status"
              value={statusFilter}
              onChange={(e) => {
                const next = new URLSearchParams(searchParams);
                if (e.target.value) next.set("status", e.target.value);
                else next.delete("status");
                setSearchParams(next, { replace: true });
                pager.reset();
              }}
            >
              <option value="">All statuses</option>
              {RUN_STATUSES.map((s) => (
                <option key={s} value={s}>
                  {RUN_STATUS_LABELS[s] ?? s}
                </option>
              ))}
            </SelectField>
          </div>

          <DataTable
            caption="Calculation runs"
            loading={list.isPending}
            error={list.error}
            rows={list.data?.data}
            empty={statusFilter ? "No matching calculation runs." : "No calculation runs yet."}
            columns={[
              {
                header: "Run",
                cell: (r: RunOut) => (
                  <Link to={`/calculations/${r.id}`} className="font-mono text-xs text-brand underline">
                    {r.id.slice(0, 8)}
                  </Link>
                ),
              },
              {
                header: "Status",
                cell: (r) => (
                  <span
                    className={
                      "inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-medium " +
                      (r.status === "failed"
                        ? "bg-danger-soft text-danger"
                        : r.status === "completed"
                          ? "bg-ok-soft text-ok"
                          : "bg-brand-soft text-brand")
                    }
                  >
                    {RUN_STATUS_LABELS[r.status] ?? r.status}
                  </span>
                ),
              },
              { header: "Run date", cell: (r) => formatDate(r.run_date) },
              { header: "As of", cell: (r) => formatDateTime(r.as_of) },
              { header: "Data cutoff", cell: (r) => formatDateTime(r.data_cutoff) },
              {
                header: "Scope",
                cell: (r) => {
                  const entries = flattenKeyValues(r.scope);
                  return entries.length === 0 ? (
                    "—"
                  ) : (
                    <span className="text-xs text-muted">
                      {entries.map((e) => `${e.key}: ${e.value}`).join(", ")}
                    </span>
                  );
                },
              },
              {
                header: "Totals / Error",
                cell: (r) => {
                  if (r.status === "failed" && r.error) {
                    return <span className="text-xs text-danger">{formatRunError(r.error)}</span>;
                  }
                  const entries = flattenKeyValues(r.totals);
                  return entries.length === 0 ? (
                    "—"
                  ) : (
                    <span className="font-mono text-xs">
                      {entries.map((e) => `${e.key}: ${e.value}`).join(", ")}
                    </span>
                  );
                },
              },
              {
                header: "Engine",
                cell: (r) => <span className="font-mono text-xs">{r.engine_version}</span>,
              },
              { header: "Created", cell: (r) => formatDateTime(r.created_at) },
            ]}
          />
          <PagerControls pager={pager} page={list.data?.page} />
        </Card>
      </div>
    </>
  );
}
