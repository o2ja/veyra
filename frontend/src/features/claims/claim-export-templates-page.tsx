import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { Alert, Button, Card, Field, PermissionDenied } from "../../components/ui";
import { api } from "../../lib/api/client";
import { ApiError, unwrap } from "../../lib/api/errors";
import { useSession } from "../../lib/session";
import {
  Checkbox,
  DataTable,
  ManufacturerSelect,
  PagerControls,
  SelectField,
  usePager,
  utc,
} from "../masterdata/common";
import { ClaimsNav, ValidationDetailsAlert, useManufacturerMap } from "./common";
import {
  SOURCE_KIND_MAP,
  TEMPLATE_SOURCES,
  defaultTemplateColumn,
  defaultTemplateOptions,
  getFormatOptionsForSource,
  type ColumnIn,
  type ExportTemplateOut,
  type OptionsIn,
} from "./logic";

interface EditorFormState {
  id?: string;
  revision?: number;
  manufacturer_id: string;
  name: string;
  format: "csv" | "xlsx";
  active: boolean;
  columns: ColumnIn[];
  options: OptionsIn;
}

export function ClaimExportTemplatesPage() {
  const { can } = useSession();
  const client = useQueryClient();
  const pager = usePager();
  const manufacturers = useManufacturerMap();

  const [filterManufacturerId, setFilterManufacturerId] = useState("");
  const [editingTemplate, setEditingTemplate] = useState<EditorFormState | null>(null);
  const [staleError, setStaleError] = useState(false);

  const list = useQuery({
    queryKey: ["claim-export-templates", filterManufacturerId, pager.cursor],
    queryFn: () =>
      unwrap(
        api.GET("/api/v1/claim-export-templates", {
          params: {
            query: {
              manufacturer_id: filterManufacturerId || null,
              limit: 50,
              cursor: pager.cursor,
            },
          },
        }),
      ),
  });

  const saveMutation = useMutation({
    mutationFn: async (form: EditorFormState) => {
      setStaleError(false);
      if (form.id && form.revision !== undefined) {
        // Edit PUT
        return unwrap(
          api.PUT("/api/v1/claim-export-templates/{export_template_id}", {
            params: {
              path: { export_template_id: form.id },
              header: { "if-match": `"${form.revision}"` },
            },
            body: {
              name: form.name.trim(),
              format: form.format,
              active: form.active,
              columns: form.columns,
              options: form.options,
            },
          }),
        );
      }
      // Create POST
      return unwrap(
        api.POST("/api/v1/claim-export-templates", {
          body: {
            manufacturer_id: form.manufacturer_id,
            name: form.name.trim(),
            format: form.format,
            active: form.active,
            columns: form.columns,
            options: form.options,
          },
        }),
      );
    },
    onSuccess: () => {
      setEditingTemplate(null);
      void client.invalidateQueries({ queryKey: ["claim-export-templates"] });
    },
    onError: (err) => {
      if (err instanceof ApiError && err.status === 412) {
        setStaleError(true);
      }
    },
  });

  if (!can("claim_batch.create")) {
    return (
      <>
        <ClaimsNav />
        <PermissionDenied />
      </>
    );
  }

  function startCreate() {
    setStaleError(false);
    saveMutation.reset();
    setEditingTemplate({
      manufacturer_id: filterManufacturerId || "",
      name: "",
      format: "csv",
      active: true,
      columns: [
        { header: "Claim Ref", source: "claim_reference", constant: null, format: null },
        { header: "Doc #", source: "document_number", constant: null, format: null },
        { header: "Invoice Date", source: "invoice_date", constant: null, format: "iso" },
        { header: "Amount", source: "claim_amount", constant: null, format: "decimal:2" },
      ],
      options: defaultTemplateOptions(),
    });
  }

  function startEdit(t: ExportTemplateOut) {
    setStaleError(false);
    saveMutation.reset();
    const cols = (t.columns ?? []).map((c: Record<string, unknown>) => ({
      header: String(c.header ?? ""),
      source: c.source ? String(c.source) : null,
      constant: c.constant ? String(c.constant) : null,
      format: c.format ? String(c.format) : null,
    }));
    const opts: OptionsIn = {
      include_header: t.options?.include_header !== false,
      delimiter: (t.options?.delimiter as OptionsIn["delimiter"]) || ",",
      sheet_name: String(t.options?.sheet_name || "Claims"),
    };
    setEditingTemplate({
      id: t.id,
      revision: t.revision,
      manufacturer_id: t.manufacturer_id,
      name: t.name,
      format: t.format,
      active: t.active,
      columns: cols.length > 0 ? cols : [defaultTemplateColumn()],
      options: opts,
    });
  }

  function addColumn() {
    if (!editingTemplate) return;
    setEditingTemplate({
      ...editingTemplate,
      columns: [...editingTemplate.columns, defaultTemplateColumn()],
    });
  }

  function removeColumn(idx: number) {
    if (!editingTemplate) return;
    setEditingTemplate({
      ...editingTemplate,
      columns: editingTemplate.columns.filter((_, i) => i !== idx),
    });
  }

  function moveColumn(idx: number, dir: -1 | 1) {
    if (!editingTemplate) return;
    const targetIdx = idx + dir;
    if (targetIdx < 0 || targetIdx >= editingTemplate.columns.length) return;
    const newCols = [...editingTemplate.columns];
    const colA = newCols[idx];
    const colB = newCols[targetIdx];
    if (!colA || !colB) return;
    newCols[idx] = colB;
    newCols[targetIdx] = colA;
    setEditingTemplate({ ...editingTemplate, columns: newCols });
  }

  function updateColumn(idx: number, patch: Partial<ColumnIn>) {
    if (!editingTemplate) return;
    const newCols = [...editingTemplate.columns];
    const col = newCols[idx];
    if (!col) return;
    newCols[idx] = { ...col, ...patch };
    setEditingTemplate({ ...editingTemplate, columns: newCols });
  }

  const listErr = list.error as ApiError | null;
  const saveErr = saveMutation.error as ApiError | null;

  return (
    <>
      <ClaimsNav />
      <div className="flex flex-col gap-6">
        <Card
          title="Claim export templates"
          actions={!editingTemplate ? <Button onClick={startCreate}>New template</Button> : undefined}
        >
          <div className="mb-4 max-w-sm">
            <ManufacturerSelect
              allowAny="All manufacturers"
              value={filterManufacturerId}
              onChange={(mId) => {
                setFilterManufacturerId(mId);
                pager.reset();
              }}
            />
          </div>

          <DataTable
            caption="Claim export templates"
            loading={list.isPending}
            error={listErr}
            rows={list.data?.data}
            empty="No export templates found."
            columns={[
              {
                header: "Template name",
                cell: (t: ExportTemplateOut) => (
                  <button
                    type="button"
                    className="font-medium text-brand underline"
                    onClick={() => startEdit(t)}
                  >
                    {t.name}
                  </button>
                ),
              },
              {
                header: "Manufacturer",
                cell: (t) => {
                  const m = manufacturers.get(t.manufacturer_id);
                  return m ? `${m.name} (${m.code})` : t.manufacturer_id;
                },
              },
              {
                header: "Format",
                cell: (t) => t.format.toUpperCase(),
              },
              {
                header: "Columns",
                cell: (t) => t.columns?.length ?? 0,
              },
              {
                header: "Status",
                cell: (t) => (t.active ? "Active" : <span className="text-muted">Inactive</span>),
              },
              {
                header: "Updated",
                cell: (t) => utc(t.updated_at),
              },
              {
                header: "Actions",
                cell: (t) => (
                  <Button variant="secondary" onClick={() => startEdit(t)}>
                    Edit
                  </Button>
                ),
              },
            ]}
          />
          <PagerControls pager={pager} page={list.data?.page} />
        </Card>

        {editingTemplate && (
          <Card
            title={
              editingTemplate.id ? `Edit template: ${editingTemplate.name}` : "Create new export template"
            }
          >
            {staleError && (
              <div className="mb-4">
                <Alert tone="danger">
                  This template was modified by another user or session. Please reload to see the latest
                  version before saving.
                </Alert>
              </div>
            )}

            <form
              className="flex flex-col gap-6"
              onSubmit={(e) => {
                e.preventDefault();
                saveMutation.mutate(editingTemplate);
              }}
            >
              <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
                {!editingTemplate.id && (
                  <ManufacturerSelect
                    label="Manufacturer"
                    required
                    value={editingTemplate.manufacturer_id}
                    onChange={(mId) => setEditingTemplate({ ...editingTemplate, manufacturer_id: mId })}
                  />
                )}
                <Field
                  label="Template name"
                  required
                  value={editingTemplate.name}
                  onChange={(e) => setEditingTemplate({ ...editingTemplate, name: e.target.value })}
                />
                <SelectField
                  label="Export format"
                  value={editingTemplate.format}
                  onChange={(e) => {
                    const fmt = e.target.value as "csv" | "xlsx";
                    setEditingTemplate({
                      ...editingTemplate,
                      format: fmt,
                      options: { ...editingTemplate.options },
                    });
                  }}
                >
                  <option value="csv">CSV (Comma-separated)</option>
                  <option value="xlsx">XLSX (Excel workbook)</option>
                </SelectField>
                <div className="flex items-center pt-6">
                  <Checkbox
                    label="Active template"
                    checked={editingTemplate.active}
                    onChange={(active) => setEditingTemplate({ ...editingTemplate, active })}
                  />
                </div>
              </div>

              {/* Options */}
              <div className="rounded-md border border-line bg-surface/30 p-4">
                <h2 className="mb-3 text-sm font-semibold">Format options</h2>
                <div className="grid gap-4 sm:grid-cols-3">
                  <div className="flex items-center pt-2">
                    <Checkbox
                      label="Include header row"
                      checked={editingTemplate.options.include_header}
                      onChange={(include_header) =>
                        setEditingTemplate({
                          ...editingTemplate,
                          options: { ...editingTemplate.options, include_header },
                        })
                      }
                    />
                  </div>
                  {editingTemplate.format === "csv" ? (
                    <SelectField
                      label="Delimiter"
                      value={editingTemplate.options.delimiter}
                      onChange={(e) =>
                        setEditingTemplate({
                          ...editingTemplate,
                          options: {
                            ...editingTemplate.options,
                            delimiter: e.target.value as OptionsIn["delimiter"],
                          },
                        })
                      }
                    >
                      <option value=",">Comma (,)</option>
                      <option value=";">Semicolon (;)</option>
                      <option value={"\t"}>Tab (\t)</option>
                      <option value="|">Pipe (|)</option>
                    </SelectField>
                  ) : (
                    <Field
                      label="Sheet name"
                      value={editingTemplate.options.sheet_name}
                      onChange={(e) =>
                        setEditingTemplate({
                          ...editingTemplate,
                          options: { ...editingTemplate.options, sheet_name: e.target.value },
                        })
                      }
                    />
                  )}
                </div>
              </div>

              {/* Column Editor */}
              <div>
                <div className="mb-3 flex items-center justify-between">
                  <h2 className="text-sm font-semibold">Columns ({editingTemplate.columns.length})</h2>
                  <Button type="button" variant="secondary" onClick={addColumn}>
                    Add column
                  </Button>
                </div>

                <div className="flex flex-col gap-3">
                  {editingTemplate.columns.map((col, idx) => {
                    const isConstant = col.constant !== null && col.constant !== undefined;
                    const formatOptions = !isConstant ? getFormatOptionsForSource(col.source) : [];
                    const colFieldPrefix = `columns[${idx}]`;
                    const colError = saveErr?.details.find(
                      (d) =>
                        d.field === colFieldPrefix ||
                        d.field.startsWith(`${colFieldPrefix}.`) ||
                        d.field.includes(`columns.${idx}`),
                    );

                    return (
                      <div
                        key={idx}
                        className={`rounded-md border p-3 ${
                          colError ? "border-danger bg-danger/5" : "border-line bg-panel"
                        }`}
                      >
                        <div className="flex flex-wrap items-end gap-3">
                          <span className="w-6 text-xs font-semibold text-muted">{idx + 1}.</span>

                          <div className="min-w-40 flex-1">
                            <Field
                              label="Header"
                              required
                              value={col.header}
                              placeholder="Column title"
                              onChange={(e) => updateColumn(idx, { header: e.target.value })}
                            />
                          </div>

                          <div className="w-36">
                            <SelectField
                              label="Value type"
                              value={isConstant ? "constant" : "source"}
                              onChange={(e) => {
                                const mode = e.target.value;
                                if (mode === "constant") {
                                  updateColumn(idx, {
                                    constant: "",
                                    source: null,
                                    format: null,
                                  });
                                } else {
                                  updateColumn(idx, {
                                    constant: null,
                                    source: "claim_reference",
                                    format: null,
                                  });
                                }
                              }}
                            >
                              <option value="source">Data field</option>
                              <option value="constant">Fixed constant</option>
                            </SelectField>
                          </div>

                          {!isConstant ? (
                            <div className="min-w-48 flex-1">
                              <SelectField
                                label="Source field"
                                value={col.source ?? ""}
                                onChange={(e) => {
                                  const src = e.target.value;
                                  const defaultFmt =
                                    SOURCE_KIND_MAP[src] === "date"
                                      ? "iso"
                                      : SOURCE_KIND_MAP[src] === "decimal"
                                        ? "decimal:2"
                                        : null;
                                  updateColumn(idx, { source: src, format: defaultFmt });
                                }}
                              >
                                <optgroup label="Text fields">
                                  {TEMPLATE_SOURCES.filter((s) => s.kind === "text").map((s) => (
                                    <option key={s.name} value={s.name}>
                                      {s.label} ({s.name})
                                    </option>
                                  ))}
                                </optgroup>
                                <optgroup label="Numeric / Quantity">
                                  {TEMPLATE_SOURCES.filter((s) => s.kind === "int").map((s) => (
                                    <option key={s.name} value={s.name}>
                                      {s.label} ({s.name})
                                    </option>
                                  ))}
                                </optgroup>
                                <optgroup label="Dates">
                                  {TEMPLATE_SOURCES.filter((s) => s.kind === "date").map((s) => (
                                    <option key={s.name} value={s.name}>
                                      {s.label} ({s.name})
                                    </option>
                                  ))}
                                </optgroup>
                                <optgroup label="Money & Decimals">
                                  {TEMPLATE_SOURCES.filter((s) => s.kind === "decimal").map((s) => (
                                    <option key={s.name} value={s.name}>
                                      {s.label} ({s.name})
                                    </option>
                                  ))}
                                </optgroup>
                              </SelectField>
                            </div>
                          ) : (
                            <div className="min-w-48 flex-1">
                              <Field
                                label="Constant value"
                                value={col.constant ?? ""}
                                placeholder="Static text"
                                onChange={(e) => updateColumn(idx, { constant: e.target.value })}
                              />
                            </div>
                          )}

                          {formatOptions.length > 0 && (
                            <div className="w-40">
                              <SelectField
                                label="Format"
                                value={col.format ?? ""}
                                onChange={(e) => updateColumn(idx, { format: e.target.value || null })}
                              >
                                <option value="">Default (raw string)</option>
                                {formatOptions.map((f) => (
                                  <option key={f.value} value={f.value}>
                                    {f.label}
                                  </option>
                                ))}
                              </SelectField>
                            </div>
                          )}

                          <div className="flex gap-1">
                            <Button
                              type="button"
                              variant="secondary"
                              disabled={idx === 0}
                              aria-label={`Move column ${idx + 1} up`}
                              onClick={() => moveColumn(idx, -1)}
                            >
                              ↑
                            </Button>
                            <Button
                              type="button"
                              variant="secondary"
                              disabled={idx === editingTemplate.columns.length - 1}
                              aria-label={`Move column ${idx + 1} down`}
                              onClick={() => moveColumn(idx, 1)}
                            >
                              ↓
                            </Button>
                            <Button
                              type="button"
                              variant="danger"
                              disabled={editingTemplate.columns.length <= 1}
                              aria-label={`Remove column ${idx + 1}`}
                              onClick={() => removeColumn(idx)}
                            >
                              ✕
                            </Button>
                          </div>
                        </div>

                        {colError && (
                          <p className="mt-2 text-xs text-danger">Column error: {colError.message}</p>
                        )}
                      </div>
                    );
                  })}
                </div>
              </div>

              <ValidationDetailsAlert error={saveErr} />

              <div className="flex gap-3">
                <Button type="submit" disabled={saveMutation.isPending}>
                  {saveMutation.isPending ? "Saving…" : "Save template"}
                </Button>
                <Button
                  type="button"
                  variant="secondary"
                  disabled={saveMutation.isPending}
                  onClick={() => setEditingTemplate(null)}
                >
                  Cancel
                </Button>
              </div>
            </form>
          </Card>
        )}
      </div>
    </>
  );
}
