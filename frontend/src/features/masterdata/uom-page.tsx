import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router-dom";

import type { components } from "../../api/schema";
import { Alert, Button, Card, ErrorAlert, Field, fieldError } from "../../components/ui";
import { api } from "../../lib/api/client";
import { ApiError, unwrap } from "../../lib/api/errors";
import { useSession } from "../../lib/session";
import { DataTable, EntityPicker, FormGrid, MasterDataNav, PagerControls, orNull, usePager } from "./common";

type Conversion = components["schemas"]["UomConversionOut"];

function FactorEditor({ c, onDone }: { c: Conversion; onDone: () => void }) {
  const [factor, setFactor] = useState(c.factor);
  const save = useMutation({
    mutationFn: () =>
      unwrap(
        api.PUT("/api/v1/uom-conversions/{conversion_id}", {
          params: { path: { conversion_id: c.id } },
          body: { factor },
        }),
      ),
    onSuccess: onDone,
  });
  const error = save.error as ApiError | null;
  return (
    <form
      className="flex items-end gap-2"
      onSubmit={(e) => {
        e.preventDefault();
        save.mutate();
      }}
    >
      <Field
        label={`Factor ${c.from_uom} to ${c.to_uom}`}
        inputMode="decimal"
        value={factor}
        error={fieldError(error, "factor") ?? error?.message}
        onChange={(e) => setFactor(e.target.value.trim())}
      />
      <Button type="submit" disabled={save.isPending}>
        Save
      </Button>
      <Button variant="secondary" onClick={onDone}>
        Cancel
      </Button>
    </form>
  );
}

/** UoM conversions, all or one product's (FR: 1 FROM = factor × TO). */
export function UomConversions({ productId }: { productId?: string }) {
  const client = useQueryClient();
  const { can } = useSession();
  const editable = can("masterdata.write");
  const pager = usePager();
  const [editing, setEditing] = useState<string | null>(null);
  const empty = { from_uom: "", to_uom: "", factor: "", product: productId ?? "" };
  const [form, setForm] = useState(empty);
  const list = useQuery({
    queryKey: ["uom", productId ?? null, pager.cursor],
    queryFn: () =>
      unwrap(
        api.GET("/api/v1/uom-conversions", {
          params: { query: { product_id: productId ?? null, cursor: pager.cursor, limit: 50 } },
        }),
      ),
  });
  const refresh = () => void client.invalidateQueries({ queryKey: ["uom"] });
  const create = useMutation({
    mutationFn: () =>
      unwrap(
        api.POST("/api/v1/uom-conversions", {
          body: {
            from_uom: form.from_uom.trim(),
            to_uom: form.to_uom.trim(),
            factor: form.factor,
            product_id: orNull(form.product),
          },
        }),
      ),
    onSuccess: () => {
      setForm(empty);
      refresh();
    },
  });
  const remove = useMutation({
    mutationFn: (id: string) =>
      unwrap(api.DELETE("/api/v1/uom-conversions/{conversion_id}", { params: { path: { conversion_id: id } } })),
    onSuccess: refresh,
  });
  const error = create.error as ApiError | null;
  return (
    <div className="flex flex-col gap-4">
      <ErrorAlert error={remove.error as ApiError | null} />
      <DataTable
        caption="Unit-of-measure conversions"
        loading={list.isPending}
        error={list.error}
        rows={list.data?.data}
        empty="No conversions."
        columns={[
          { header: "From", cell: (c) => c.from_uom },
          { header: "To", cell: (c) => c.to_uom },
          {
            header: "Factor",
            cell: (c) =>
              editing === c.id ? (
                <FactorEditor
                  c={c}
                  onDone={() => {
                    setEditing(null);
                    refresh();
                  }}
                />
              ) : (
                <span className="font-mono">{c.factor}</span>
              ),
          },
          {
            header: "Product",
            cell: (c) =>
              c.product_id ? (
                <Link to={`/master-data/products/${c.product_id}`} className="text-brand underline">
                  Open product
                </Link>
              ) : (
                "All products"
              ),
          },
          {
            header: "Actions",
            srOnly: true,
            cell: (c) =>
              editable && (
                <div className="flex gap-3">
                  <button
                    type="button"
                    className="text-brand underline"
                    aria-label={`Edit ${c.from_uom} to ${c.to_uom}`}
                    onClick={() => setEditing(c.id)}
                  >
                    Edit
                  </button>
                  <button
                    type="button"
                    className="text-danger underline"
                    aria-label={`Delete ${c.from_uom} to ${c.to_uom}`}
                    disabled={remove.isPending}
                    onClick={() => {
                      if (window.confirm(`Delete the conversion ${c.from_uom} → ${c.to_uom}?`))
                        remove.mutate(c.id);
                    }}
                  >
                    Delete
                  </button>
                </div>
              ),
          },
        ]}
      />
      <PagerControls pager={pager} page={list.data?.page} />
      {editable && (
        <form
          className="flex flex-col gap-3 border-t border-line pt-4"
          onSubmit={(e) => {
            e.preventDefault();
            create.mutate();
          }}
        >
          <h2 className="text-sm font-semibold">Add a conversion</h2>
          <FormGrid>
            <Field
              label="From unit"
              required
              value={form.from_uom}
              error={fieldError(error, "from_uom")}
              onChange={(e) => setForm({ ...form, from_uom: e.target.value })}
            />
            <Field
              label="To unit"
              required
              value={form.to_uom}
              error={fieldError(error, "to_uom")}
              onChange={(e) => setForm({ ...form, to_uom: e.target.value })}
            />
            <Field
              label="Factor"
              required
              inputMode="decimal"
              hint="1 from-unit = factor × to-unit"
              value={form.factor}
              error={fieldError(error, "factor")}
              onChange={(e) => setForm({ ...form, factor: e.target.value.trim() })}
            />
          </FormGrid>
          {!productId && (
            <EntityPicker
              kind="product"
              label="Product (optional, empty = all products)"
              value={form.product}
              onChange={(v) => setForm({ ...form, product: v })}
            />
          )}
          {create.isSuccess && <Alert tone="ok">Conversion added.</Alert>}
          <ErrorAlert error={error} />
          <div>
            <Button type="submit" disabled={create.isPending}>
              Add conversion
            </Button>
          </div>
        </form>
      )}
    </div>
  );
}

export function UomPage() {
  return (
    <>
      <MasterDataNav />
      <Card title="Unit-of-measure conversions">
        <UomConversions />
      </Card>
    </>
  );
}
