import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";

import type { components } from "../../api/schema";
import { Alert, Button, Card, ErrorAlert, Field, Loading, fieldError } from "../../components/ui";
import { api } from "../../lib/api/client";
import { ApiError, unwrap } from "../../lib/api/errors";
import { useSession } from "../../lib/session";
import { ConfirmDialog } from "../claims/common";
import { Checkbox, DataTable, PagerControls, SelectField, usePager } from "../masterdata/common";
import {
  type Mapping,
  STATUS_TEXT,
  TYPE_TEXT,
  cleanDefaults,
  initialMapping,
  isBusy,
  label,
  stepOf,
  unmappedRequired,
  usableSuggestion,
} from "./logic";
import { formatDateTime } from "../../lib/format";

type ImportOut = components["schemas"]["ImportOut"];
type Preview = components["schemas"]["PreviewOut"];
type Options = components["schemas"]["MappingOptions"];

type RowErrorList = components["schemas"]["RowErrorList"];
type Suggestion = components["schemas"]["MappingOut"];

function Counts({ i }: { i: ImportOut }) {
  const c = i.counts;
  const items: [string, number][] = [
    ["Rows in file", c.rows_total],
    ["Valid", c.valid],
    ["Duplicates in file", c.duplicates],
    ["Rejected", c.rejected],
    ["Warnings", c.warnings],
    ["Inserted", c.inserted],
    ["Changed", c.changed],
    ["Unchanged", c.unchanged],
  ];
  return (
    <dl aria-label="Row counts" className="grid grid-cols-2 gap-x-6 gap-y-1 text-sm sm:grid-cols-4">
      {items.map(([k, v]) => (
        <div key={k} className="flex justify-between gap-2 border-b border-line py-1">
          <dt className="text-muted">{k}</dt>
          <dd className="font-mono">{v}</dd>
        </div>
      ))}
    </dl>
  );
}

function MappingForm({ i, preview, onDone }: { i: ImportOut; preview: Preview; onDone: () => void }) {
  const [mapping, setMapping] = useState<Mapping>(() =>
    initialMapping(preview.fields, preview.suggested_mapping, i.mapping ?? null),
  );
  const opts = i.options as Options;
  const [defaults, setDefaults] = useState<Record<string, string>>(opts.defaults ?? {});
  const [dateOrder, setDateOrder] = useState<NonNullable<Options["date_order"]>>(opts.date_order ?? "MDY");
  const [decimal, setDecimal] = useState<NonNullable<Options["decimal_separator"]>>(
    opts.decimal_separator ?? ".",
  );
  const [template, setTemplate] = useState("");
  const [ai, setAi] = useState<ApiError | null>(null);
  const [suggestion, setSuggestion] = useState<Suggestion | null>(null);
  const [asking, setAsking] = useState(false);
  const save = useMutation({
    mutationFn: () =>
      unwrap(
        api.PUT("/api/v1/imports/{import_id}/mapping", {
          params: { path: { import_id: i.id } },
          body: {
            mapping,
            options: {
              date_order: dateOrder,
              decimal_separator: decimal,
              defaults: cleanDefaults(mapping, defaults),
            },
            save_as_template: template.trim() ? { name: template.trim() } : null,
          },
        }),
      ),
    onSuccess: onDone,
  });
  async function suggest() {
    setAi(null);
    setSuggestion(null);
    setAsking(true);
    try {
      setSuggestion(
        await unwrap(
          api.POST("/api/v1/imports/{import_id}/suggest-mapping", { params: { path: { import_id: i.id } } }),
        ),
      );
    } catch (e) {
      setAi(e instanceof ApiError ? e : null);
    } finally {
      setAsking(false);
    }
  }
  const suggested = suggestion ? usableSuggestion(preview.fields, preview.headers, suggestion.mapping) : null;
  const error = save.error as ApiError | null;
  const missing = unmappedRequired(preview.fields, mapping, defaults);
  return (
    <form
      className="flex flex-col gap-4"
      onSubmit={(e) => {
        e.preventDefault();
        save.mutate();
      }}
    >
      <p className="text-sm text-muted">
        Choose the column for each field. The choices are pre-filled from the column names
        {preview.template ? ` and the template “${preview.template.name}”` : ""}; check them before you
        continue.
      </p>
      <div className="overflow-x-auto" tabIndex={0} role="region" aria-label="Column mapping">
        <table className="w-full text-left text-sm">
          <caption className="sr-only">Column mapping</caption>
          <thead className="text-muted">
            <tr>
              <th scope="col" className="py-2 pr-4">
                Field
              </th>
              <th scope="col" className="pr-4">
                Column in the file
              </th>
              <th scope="col">Fixed value if not in the file</th>
            </tr>
          </thead>
          <tbody>
            {preview.fields.map((f) => (
              <tr key={f.name} className="border-t border-line">
                <th scope="row" className="py-2 pr-4 font-normal">
                  {label(f.name)}
                  {f.required && <span className="text-danger"> (required)</span>}
                  {fieldError(error, `mapping.${f.name}`) && (
                    <span className="block text-xs text-danger">
                      {fieldError(error, `mapping.${f.name}`)}
                    </span>
                  )}
                </th>
                <td className="pr-4">
                  <select
                    aria-label={`Column for ${label(f.name)}`}
                    className="rounded-md border border-line bg-panel px-2 py-1 text-sm"
                    value={mapping[f.name] ?? ""}
                    onChange={(e) => setMapping({ ...mapping, [f.name]: e.target.value || null })}
                  >
                    <option value="">Not in the file</option>
                    {preview.headers.map((h) => (
                      <option key={h} value={h}>
                        {h}
                      </option>
                    ))}
                  </select>
                </td>
                <td>
                  {!mapping[f.name] && (
                    <input
                      aria-label={`Fixed value for ${label(f.name)}`}
                      className="rounded-md border border-line bg-panel px-2 py-1 text-sm"
                      value={defaults[f.name] ?? ""}
                      onChange={(e) => setDefaults({ ...defaults, [f.name]: e.target.value })}
                    />
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="grid max-w-2xl gap-3 sm:grid-cols-3">
        <SelectField
          label="Date order"
          value={dateOrder}
          onChange={(e) => setDateOrder(e.target.value as "MDY" | "DMY")}
        >
          <option value="MDY">Month/day/year (6/12/2026 = June 12)</option>
          <option value="DMY">Day/month/year (6/12/2026 = 6 December)</option>
        </SelectField>
        <SelectField
          label="Decimal separator"
          value={decimal}
          onChange={(e) => setDecimal(e.target.value as "." | ",")}
        >
          <option value=".">Point (1,234.56)</option>
          <option value=",">Comma (1.234,56)</option>
        </SelectField>
        <Field
          label="Save as template (optional)"
          hint="Name; files with the same headers reuse it"
          value={template}
          error={fieldError(error, "save_as_template.name")}
          onChange={(e) => setTemplate(e.target.value)}
        />
      </div>
      {missing.length > 0 && <Alert tone="info">Still needed: {missing.map(label).join(", ")}.</Alert>}
      {ai && <Alert tone="info">{ai.message}</Alert>}
      {suggestion && suggested && (
        <div className="rounded-md border border-line p-3 text-sm" role="region" aria-label="AI suggestion">
          <p className="font-medium">AI suggestion (not saved)</p>
          <ul className="my-2">
            {preview.fields.map((f) => (
              <li key={f.name}>
                {label(f.name)}: <span className="font-mono">{suggested[f.name] ?? "not in the file"}</span>
              </li>
            ))}
          </ul>
          <Button
            variant="secondary"
            onClick={() => {
              setMapping(suggested);
              setSuggestion(null);
            }}
          >
            Apply suggestion
          </Button>
          <span className="ml-2 text-muted">Applying only fills the form above; check it, then save.</span>
        </div>
      )}
      <ErrorAlert error={error} />
      <div className="flex flex-wrap gap-3">
        <Button type="submit" disabled={save.isPending}>
          Apply mapping and check rows
        </Button>
        <Button variant="secondary" disabled={asking} onClick={() => void suggest()}>
          Suggest with AI
        </Button>
      </div>
    </form>
  );
}

function PreviewRows({ preview }: { preview: Preview }) {
  const rows = preview.rows.map((r) => ({ ...r, id: String(r.row_number) }));
  return (
    <DataTable
      caption="First rows of the file"
      loading={false}
      error={null}
      rows={rows}
      empty="No rows."
      columns={[
        { header: "Row", cell: (r) => r.row_number },
        { header: "Check", cell: (r) => (r.status === "valid" ? "OK" : r.status) },
        {
          header: "Issues",
          cell: (r) =>
            r.issues.length ? (
              <ul className="text-xs">
                {r.issues.map((x, n) => (
                  <li key={n} className={x.severity === "error" ? "text-danger" : ""}>
                    {x.column ? `${x.column}: ` : ""}
                    {x.message} <span className="font-mono">({x.code})</span>
                  </li>
                ))}
              </ul>
            ) : (
              "—"
            ),
        },
        ...preview.headers.map((h) => ({ header: h, cell: (r: (typeof rows)[number]) => r.raw[h] ?? "" })),
      ]}
    />
  );
}

function RowErrors({ i }: { i: ImportOut }) {
  const pager = usePager();
  const list = useQuery({
    queryKey: ["imports", i.id, "errors", i.mapping_revision, i.status, pager.cursor],
    queryFn: async () =>
      // the operation also lists text/csv (?format=csv), so the client's body type is a union: narrow to JSON
      (await unwrap(
        api.GET("/api/v1/imports/{import_id}/errors", {
          params: { path: { import_id: i.id }, query: { cursor: pager.cursor, limit: 50 } },
        }),
      )) as RowErrorList,
  });
  return (
    <>
      <DataTable
        caption="Row errors"
        loading={list.isPending}
        error={list.error}
        rows={list.data?.data}
        empty="No row errors."
        columns={[
          { header: "Row", cell: (e) => e.row_number ?? "File" },
          { header: "Column", cell: (e) => e.column_name ?? "—" },
          { header: "Severity", cell: (e) => (e.severity === "error" ? "Error" : "Warning") },
          { header: "Code", cell: (e) => <span className="font-mono">{e.code}</span> },
          { header: "Message", cell: (e) => e.message },
        ]}
      />
      <PagerControls pager={pager} page={list.data?.page} />
      {Boolean(list.data?.data.length) && (
        <a
          href={`/api/v1/imports/${i.id}/errors?format=csv`}
          className="mt-3 inline-block text-brand underline"
        >
          Download all row errors (CSV)
        </a>
      )}
    </>
  );
}

function Changes({ i }: { i: ImportOut }) {
  const pager = usePager();
  const list = useQuery({
    queryKey: ["imports", i.id, "changes", pager.cursor],
    queryFn: () =>
      unwrap(
        api.GET("/api/v1/imports/{import_id}/changes", {
          params: { path: { import_id: i.id }, query: { cursor: pager.cursor, limit: 50 } },
        }),
      ),
  });
  return (
    <>
      <DataTable
        caption="Changed rows"
        loading={list.isPending}
        error={list.error}
        rows={list.data?.data.map((c) => ({ ...c, id: c.line_id }))}
        empty="No row changed compared with earlier imports."
        columns={[
          {
            header: "Line",
            cell: (c) =>
              i.import_type === "sales" ? (
                <Link to={`/lines/sales/${c.line_id}`} className="text-brand underline">
                  {c.document_number} / {c.line_number}
                </Link>
              ) : (
                `${c.document_number} / ${c.line_number}`
              ),
          },
          { header: "Source row", cell: (c) => c.source_row_number },
          { header: "Version", cell: (c) => c.version },
          {
            header: "Changed fields (before → after)",
            cell: (c) => (
              <ul className="text-xs">
                {Object.entries(c.changed_fields).map(([field, v]) => {
                  const [before, after] = v;
                  return (
                    <li key={field}>
                      {label(field)}: <span className="font-mono">{before ?? "—"}</span> →{" "}
                      <span className="font-mono">{after ?? "—"}</span>
                    </li>
                  );
                })}
              </ul>
            ),
          },
          {
            header: "Already claimed",
            cell: (c) => (c.source_changed ? "Yes: review the claim" : "No"),
          },
          {
            header: "Previous import",
            cell: (c) => (
              <Link to={`/imports/${c.previous_import_batch_id}`} className="text-brand underline">
                Open
              </Link>
            ),
          },
        ]}
      />
      <PagerControls pager={pager} page={list.data?.page} />
    </>
  );
}

function Review({
  i,
  preview,
  onMapAgain,
}: {
  i: ImportOut;
  preview: Preview | undefined;
  onMapAgain: () => void;
}) {
  const client = useQueryClient();
  const [partial, setPartial] = useState(false);
  const confirm = useMutation({
    mutationFn: () =>
      unwrap(
        api.POST("/api/v1/imports/{import_id}/confirm", {
          params: { path: { import_id: i.id } },
          body: { accept_partial: partial },
        }),
      ),
    onSuccess: () => void client.invalidateQueries({ queryKey: ["imports", i.id] }),
  });
  const rejected = i.counts.rejected;
  return (
    <div className="flex flex-col gap-4">
      {rejected > 0 ? (
        <Alert>
          {rejected} of {i.counts.rows_total} rows have errors and cannot be loaded. Fix the file and import
          it again, or load only the valid rows.
        </Alert>
      ) : (
        <Alert tone="ok">All {i.counts.valid} rows passed the checks.</Alert>
      )}
      {preview && <PreviewRows preview={preview} />}
      {rejected > 0 && (
        <Checkbox
          label={`Load the ${i.counts.valid} valid rows and skip the ${rejected} rejected rows`}
          checked={partial}
          onChange={setPartial}
        />
      )}
      <ErrorAlert error={confirm.error as ApiError | null} />
      {(confirm.error as ApiError | null)?.code === "too_many_running_imports" && (
        <Link to="/imports" className="text-brand underline">
          See the imports being processed
        </Link>
      )}
      <div className="flex flex-wrap gap-3">
        <Button disabled={confirm.isPending || (rejected > 0 && !partial)} onClick={() => confirm.mutate()}>
          Confirm import
        </Button>
        <Button variant="secondary" onClick={onMapAgain}>
          Change the mapping
        </Button>
      </div>
    </div>
  );
}

/** One import: the wizard after step 1 (mapping, preview, errors, confirm, progress) and its results. */
export function ImportDetailPage() {
  const { importId = "" } = useParams();
  const client = useQueryClient();
  const { can } = useSession();
  const navigate = useNavigate();
  const [remap, setRemap] = useState(false);
  const [discarding, setDiscarding] = useState(false);
  const path = { import_id: importId };
  const discard = useMutation({
    mutationFn: () => unwrap(api.POST("/api/v1/imports/{import_id}/discard", { params: { path } })),
    onSuccess: () => {
      setDiscarding(false);
      void client.invalidateQueries({ queryKey: ["imports"] });
      navigate("/imports");
    },
  });
  const batch = useQuery({
    queryKey: ["imports", importId],
    queryFn: () => unwrap(api.GET("/api/v1/imports/{import_id}", { params: { path } })),
    refetchInterval: (q) => (q.state.data && isBusy(q.state.data) ? 1500 : false),
  });
  const i = batch.data;
  const step = i ? stepOf(i) : null;
  const preview = useQuery({
    queryKey: ["imports", importId, "preview", i?.mapping_revision, i?.status],
    queryFn: () => unwrap(api.GET("/api/v1/imports/{import_id}/preview", { params: { path } })),
    enabled: can("import.run") && (step === "map" || step === "review"),
  });
  const refresh = () => {
    setRemap(false);
    void client.invalidateQueries({ queryKey: ["imports", importId] });
  };
  const lines = i?.import_type === "purchases" ? "purchases" : "sales";

  return (
    <div className="flex flex-col gap-6">
      <Card
        title={i ? `${TYPE_TEXT[i.import_type] ?? i.import_type} import` : "Import"}
        actions={
          i?.status === "awaiting_confirmation" && can("import.run") ? (
            <Button variant="secondary" onClick={() => setDiscarding(true)}>
              Discard import
            </Button>
          ) : undefined
        }
      >
        <ErrorAlert error={(batch.error ?? discard.error) as ApiError | null} />
        <ConfirmDialog
          open={discarding}
          onOpenChange={setDiscarding}
          title="Discard this import?"
          description="Nothing from this file has been loaded yet. The import is closed and kept in the history; the file stays in Files. This is recorded in the audit log."
          confirmLabel="Discard import"
          confirmVariant="danger"
          busy={discard.isPending}
          onConfirm={() => discard.mutate()}
        />
        {batch.isPending && <Loading />}
        {i && (
          <div className="flex flex-col gap-4">
            <dl className="grid grid-cols-[max-content_1fr] gap-x-6 gap-y-1 text-sm">
              <dt className="text-muted">Status</dt>
              <dd>
                <span role="status" aria-live="polite">
                  {STATUS_TEXT[i.status] ?? i.status}
                </span>
              </dd>
              <dt className="text-muted">File</dt>
              <dd>
                <Link to={`/files/${i.file_id}`} className="text-brand underline">
                  Open source file
                </Link>
              </dd>
              <dt className="text-muted">Source system</dt>
              <dd>{i.source_system}</dd>
              <dt className="text-muted">Format</dt>
              <dd>
                {[
                  i.detected_encoding,
                  i.delimiter && `delimiter “${i.delimiter}”`,
                  i.sheet_name && `sheet ${i.sheet_name}`,
                  i.header_row && `header in row ${i.header_row}`,
                ]
                  .filter(Boolean)
                  .join(", ") || "—"}
              </dd>
              <dt className="text-muted">Started</dt>
              <dd>{formatDateTime(i.created_at)}</dd>
              {i.completed_at && (
                <>
                  <dt className="text-muted">Completed</dt>
                  <dd>{formatDateTime(i.completed_at)}</dd>
                </>
              )}
            </dl>
            <Counts i={i} />
          </div>
        )}
      </Card>

      {i && step === "processing" && (
        <Card title="Reading the file">
          <Loading label={STATUS_TEXT[i.status] ?? "Working…"} />
        </Card>
      )}
      {i && step === "discarded" && (
        <Card title="Discarded">
          <Alert tone="info">This import was discarded before any rows were loaded.</Alert>
          <Link to="/imports/new" className="mt-3 inline-block text-brand underline">
            Start a new import
          </Link>
        </Card>
      )}
      {i && step === "failed" && (
        <Card title="The import stopped">
          <Alert>{i.error_summary ?? "The import failed."}</Alert>
          <Link to="/imports/new" className="mt-3 inline-block text-brand underline">
            Start a new import
          </Link>
        </Card>
      )}
      {i && (step === "map" || (step === "review" && remap)) && (
        <Card title="Map the columns">
          {!can("import.run") ? (
            <Alert tone="info">Waiting for an analyst to map the columns.</Alert>
          ) : preview.data ? (
            <MappingForm key={i.mapping_revision} i={i} preview={preview.data} onDone={refresh} />
          ) : (
            <>
              <ErrorAlert error={preview.error as ApiError | null} />
              {preview.isPending && <Loading />}
            </>
          )}
        </Card>
      )}
      {i && step === "review" && !remap && (
        <Card title="Check and confirm">
          {can("import.run") ? (
            <Review i={i} preview={preview.data} onMapAgain={() => setRemap(true)} />
          ) : (
            <Alert tone="info">Waiting for an analyst to confirm the import.</Alert>
          )}
        </Card>
      )}
      {i && step === "loading" && (
        <Card title="Loading rows">
          <progress
            className="w-full"
            aria-label="Rows loaded"
            max={Math.max(i.counts.valid, 1)}
            value={i.counts.inserted + i.counts.changed + i.counts.unchanged}
          />
        </Card>
      )}
      {i && step === "done" && (
        <Card title="Result">
          {i.status === "completed_with_errors" ? (
            <Alert tone="info">
              Loaded the valid rows; {i.counts.rejected} rows were rejected (see below).
            </Alert>
          ) : (
            <Alert tone="ok">Import completed.</Alert>
          )}
          {(i.import_type === "sales" || i.import_type === "purchases") && (
            <Link
              to={`/lines/${lines}?import_batch_id=${i.id}`}
              className="mt-3 inline-block text-brand underline"
            >
              Browse the lines of this import
            </Link>
          )}
        </Card>
      )}
      {i && step !== "processing" && step !== "discarded" && (
        <Card title="Row errors">
          <RowErrors i={i} />
        </Card>
      )}
      {i && step === "done" && (i.import_type === "sales" || i.import_type === "purchases") && (
        <Card title="Changed rows">
          <Changes i={i} />
        </Card>
      )}
      <Link to="/imports" className="text-brand underline">
        Back to imports
      </Link>
    </div>
  );
}
