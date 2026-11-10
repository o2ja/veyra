import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useParams } from "react-router-dom";

import type { components } from "../../api/schema";
import { Alert, Button, Card, ErrorAlert, Field, Loading, fieldError } from "../../components/ui";
import { api } from "../../lib/api/client";
import { ApiError, unwrap } from "../../lib/api/errors";
import { useSession } from "../../lib/session";
import {
  Checkbox,
  DataTable,
  FormGrid,
  ManufacturerSelect,
  MasterDataNav,
  PagerControls,
  SelectField,
  orNull,
  useManufacturers,
  usePager,
  utc,
} from "./common";
import { UomConversions } from "./uom-page";

type Product = components["schemas"]["ProductDetail"];
type AliasType = components["schemas"]["ProductAliasIn"]["alias_type"];

const ALIAS_TYPES: [AliasType, string][] = [
  ["erp_sku", "ERP SKU"],
  ["manufacturer_part", "Manufacturer part number"],
  ["upc", "UPC"],
  ["other", "Other"],
];

const blank = {
  sku: "",
  description: "",
  manufacturer_id: "",
  manufacturer_part_number: "",
  base_uom: "EA",
  category: "",
  list_cost: "",
  active: true,
};
type ProductForm = typeof blank;

function ProductFields({
  form,
  setForm,
  error,
  skuLocked,
}: {
  form: ProductForm;
  setForm: (f: ProductForm) => void;
  error: ApiError | null;
  skuLocked?: boolean;
}) {
  return (
    <FormGrid>
      <Field
        label="SKU"
        required
        disabled={skuLocked}
        hint={skuLocked ? "The SKU cannot be changed." : "Leading zeros are kept"}
        value={form.sku}
        error={fieldError(error, "sku")}
        onChange={(e) => setForm({ ...form, sku: e.target.value })}
      />
      <Field
        label="Description"
        value={form.description}
        onChange={(e) => setForm({ ...form, description: e.target.value })}
      />
      <ManufacturerSelect
        required
        value={form.manufacturer_id}
        onChange={(v) => setForm({ ...form, manufacturer_id: v })}
      />
      <Field
        label="Manufacturer part number"
        value={form.manufacturer_part_number}
        onChange={(e) => setForm({ ...form, manufacturer_part_number: e.target.value })}
      />
      <Field
        label="Base unit of measure"
        required
        hint="For example EA, FT, RL"
        value={form.base_uom}
        error={fieldError(error, "base_uom")}
        onChange={(e) => setForm({ ...form, base_uom: e.target.value })}
      />
      <Field
        label="Category"
        value={form.category}
        onChange={(e) => setForm({ ...form, category: e.target.value })}
      />
      <Field
        label="List cost"
        inputMode="decimal"
        value={form.list_cost}
        error={fieldError(error, "list_cost")}
        onChange={(e) => setForm({ ...form, list_cost: e.target.value.trim() })}
      />
    </FormGrid>
  );
}

const body = (f: ProductForm) => ({
  description: orNull(f.description),
  manufacturer_id: f.manufacturer_id,
  manufacturer_part_number: orNull(f.manufacturer_part_number),
  base_uom: f.base_uom.trim(),
  category: orNull(f.category),
  list_cost: orNull(f.list_cost), // decimal string; the server validates scale and sign
  active: f.active,
});

function CreateProduct() {
  const client = useQueryClient();
  const [form, setForm] = useState(blank);
  const create = useMutation({
    mutationFn: () => unwrap(api.POST("/api/v1/products", { body: { sku: form.sku, ...body(form) } })),
    onSuccess: () => {
      setForm(blank);
      void client.invalidateQueries({ queryKey: ["products"] });
    },
  });
  const error = create.error as ApiError | null;
  return (
    <form
      className="flex flex-col gap-3"
      onSubmit={(e) => {
        e.preventDefault();
        create.mutate();
      }}
    >
      <ProductFields form={form} setForm={setForm} error={error} />
      {create.isSuccess && <Alert tone="ok">Product added.</Alert>}
      <ErrorAlert error={error} />
      <div>
        <Button type="submit" disabled={create.isPending}>
          Add product
        </Button>
      </div>
    </form>
  );
}

export function ProductsPage() {
  const { can } = useSession();
  const pager = usePager();
  const [q, setQ] = useState("");
  const [manufacturer, setManufacturer] = useState("");
  const manufacturers = useManufacturers();
  const names = new Map(manufacturers.data?.data.map((m) => [m.id, m.code]));
  const list = useQuery({
    queryKey: ["products", q, manufacturer, pager.cursor],
    queryFn: () =>
      unwrap(
        api.GET("/api/v1/products", {
          params: {
            query: { q: q || null, manufacturer_id: manufacturer || null, cursor: pager.cursor, limit: 50 },
          },
        }),
      ),
  });
  return (
    <>
      <MasterDataNav />
      <div className="flex flex-col gap-6">
        {can("masterdata.write") && (
          <Card title="Add a product">
            <CreateProduct />
          </Card>
        )}
        <Card title="Products">
          <div className="mb-4 grid max-w-2xl gap-3 sm:grid-cols-2">
            <Field
              label="Search"
              type="search"
              hint="SKU or description"
              value={q}
              onChange={(e) => {
                setQ(e.target.value);
                pager.reset();
              }}
            />
            <ManufacturerSelect
              label="Filter by manufacturer"
              allowAny="All manufacturers"
              value={manufacturer}
              onChange={(v) => {
                setManufacturer(v);
                pager.reset();
              }}
            />
          </div>
          <DataTable
            caption="Products"
            loading={list.isPending}
            error={list.error}
            rows={list.data?.data}
            empty={
              q || manufacturer ? "No product matches." : "No products yet. Import a product file or add one."
            }
            columns={[
              {
                header: "SKU",
                cell: (p) => (
                  <Link to={`/master-data/products/${p.id}`} className="font-mono text-brand underline">
                    {p.sku}
                  </Link>
                ),
              },
              { header: "Description", cell: (p) => p.description ?? "—" },
              { header: "Manufacturer", cell: (p) => names.get(p.manufacturer_id) ?? "—" },
              { header: "Base UoM", cell: (p) => p.base_uom },
              { header: "List cost", cell: (p) => p.list_cost ?? "—" },
              { header: "Status", cell: (p) => (p.active ? "Active" : "Inactive") },
            ]}
          />
          <PagerControls pager={pager} page={list.data?.page} />
        </Card>
      </div>
    </>
  );
}

function EditProduct({ p }: { p: Product }) {
  const client = useQueryClient();
  const { can } = useSession();
  const [form, setForm] = useState<ProductForm>({
    sku: p.sku,
    description: p.description ?? "",
    manufacturer_id: p.manufacturer_id,
    manufacturer_part_number: p.manufacturer_part_number ?? "",
    base_uom: p.base_uom,
    category: p.category ?? "",
    list_cost: p.list_cost ?? "",
    active: p.active,
  });
  const save = useMutation({
    mutationFn: () =>
      unwrap(
        api.PATCH("/api/v1/products/{product_id}", {
          params: { path: { product_id: p.id } },
          body: body(form),
        }),
      ),
    onSuccess: () => void client.invalidateQueries({ queryKey: ["products"] }),
  });
  const error = save.error as ApiError | null;
  const editable = can("masterdata.write");
  return (
    <form
      className="flex flex-col gap-3"
      onSubmit={(e) => {
        e.preventDefault();
        save.mutate();
      }}
    >
      <fieldset disabled={!editable} className="flex flex-col gap-3">
        <ProductFields form={form} setForm={setForm} error={error} skuLocked />
        <Checkbox label="Active" checked={form.active} onChange={(v) => setForm({ ...form, active: v })} />
      </fieldset>
      {save.isSuccess && <Alert tone="ok">Saved.</Alert>}
      <ErrorAlert error={error} />
      {editable && (
        <div>
          <Button type="submit" disabled={save.isPending}>
            Save
          </Button>
        </div>
      )}
    </form>
  );
}

function AddProductAlias({ productId }: { productId: string }) {
  const client = useQueryClient();
  const [alias, setAlias] = useState("");
  const [type, setType] = useState<AliasType>("erp_sku");
  const add = useMutation({
    mutationFn: () =>
      unwrap(
        api.POST("/api/v1/products/{product_id}/aliases", {
          params: { path: { product_id: productId } },
          body: { alias, alias_type: type },
        }),
      ),
    onSuccess: () => {
      setAlias("");
      void client.invalidateQueries({ queryKey: ["products", productId] });
    },
  });
  const error = add.error as ApiError | null;
  return (
    <form
      className="mt-4 flex flex-col gap-2 sm:flex-row sm:items-end"
      onSubmit={(e) => {
        e.preventDefault();
        add.mutate();
      }}
    >
      <Field
        label="New alias"
        required
        value={alias}
        error={fieldError(error, "alias")}
        onChange={(e) => setAlias(e.target.value)}
      />
      <SelectField label="Alias type" value={type} onChange={(e) => setType(e.target.value as AliasType)}>
        {ALIAS_TYPES.map(([v, t]) => (
          <option key={v} value={v}>
            {t}
          </option>
        ))}
      </SelectField>
      <Button type="submit" disabled={add.isPending}>
        Add alias
      </Button>
      {!fieldError(error, "alias") && <ErrorAlert error={error} />}
    </form>
  );
}

export function ProductDetailPage() {
  const { productId = "" } = useParams();
  const { can } = useSession();
  const p = useQuery({
    queryKey: ["products", productId],
    queryFn: () =>
      unwrap(api.GET("/api/v1/products/{product_id}", { params: { path: { product_id: productId } } })),
  });
  return (
    <>
      <MasterDataNav />
      <div className="flex flex-col gap-6">
        <Card title={p.data ? `Product ${p.data.sku}` : "Product"}>
          <ErrorAlert error={p.error as ApiError | null} />
          {p.isPending ? <Loading /> : p.data && <EditProduct key={p.data.id} p={p.data} />}
        </Card>
        <Card title="Aliases">
          <DataTable
            caption="Product aliases"
            loading={p.isPending}
            error={null}
            rows={p.data?.aliases}
            empty="No aliases."
            columns={[
              { header: "Alias", cell: (a) => <span className="font-mono">{a.alias}</span> },
              {
                header: "Type",
                cell: (a) => ALIAS_TYPES.find(([v]) => v === a.alias_type)?.[1] ?? a.alias_type,
              },
              { header: "Added (UTC)", cell: (a) => utc(a.created_at) },
            ]}
          />
          {can("masterdata.write") && <AddProductAlias productId={productId} />}
        </Card>
        <Card title="Unit conversions for this product">
          <UomConversions productId={productId} />
        </Card>
        <Link to="/master-data/products" className="text-brand underline">
          Back to products
        </Link>
      </div>
    </>
  );
}
