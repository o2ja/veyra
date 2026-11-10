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
  EntityPicker,
  FormGrid,
  MasterDataNav,
  PagerControls,
  SelectField,
  orNull,
  usePager,
  utc,
} from "./common";

type CustomerType = NonNullable<components["schemas"]["CustomerIn"]["customer_type"]>;
type Customer = components["schemas"]["CustomerDetail"];

const TYPES: [CustomerType, string][] = [
  ["contractor", "Contractor"],
  ["end_user", "End user"],
  ["branch", "Branch"],
  ["other", "Other"],
];

function TypeSelect({ value, onChange }: { value: string; onChange: (v: CustomerType) => void }) {
  return (
    <SelectField label="Type" value={value} onChange={(e) => onChange(e.target.value as CustomerType)}>
      {TYPES.map(([v, t]) => (
        <option key={v} value={v}>
          {t}
        </option>
      ))}
    </SelectField>
  );
}

function CreateCustomer() {
  const client = useQueryClient();
  const empty = { name: "", code: "", external_id: "", customer_type: "contractor" as CustomerType, parent: "" };
  const [form, setForm] = useState(empty);
  const create = useMutation({
    mutationFn: () =>
      unwrap(
        api.POST("/api/v1/customers", {
          body: {
            name: form.name,
            code: orNull(form.code),
            external_id: orNull(form.external_id),
            customer_type: form.customer_type,
            parent_customer_id: orNull(form.parent),
          },
        }),
      ),
    onSuccess: () => {
      setForm(empty);
      void client.invalidateQueries({ queryKey: ["customers"] });
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
      <FormGrid>
        <Field
          label="Name"
          required
          value={form.name}
          error={fieldError(error, "name")}
          onChange={(e) => setForm({ ...form, name: e.target.value })}
        />
        <Field
          label="Code"
          value={form.code}
          error={fieldError(error, "code")}
          onChange={(e) => setForm({ ...form, code: e.target.value })}
        />
        <Field
          label="External ID"
          value={form.external_id}
          onChange={(e) => setForm({ ...form, external_id: e.target.value })}
        />
        <TypeSelect value={form.customer_type} onChange={(v) => setForm({ ...form, customer_type: v })} />
      </FormGrid>
      <EntityPicker
        kind="customer"
        label="Parent customer (optional)"
        value={form.parent}
        onChange={(v) => setForm({ ...form, parent: v })}
      />
      {create.isSuccess && <Alert tone="ok">Customer added.</Alert>}
      <ErrorAlert error={error} />
      <div>
        <Button type="submit" disabled={create.isPending}>
          Add customer
        </Button>
      </div>
    </form>
  );
}

export function CustomersPage() {
  const { can } = useSession();
  const pager = usePager();
  const [q, setQ] = useState("");
  const list = useQuery({
    queryKey: ["customers", q, pager.cursor],
    queryFn: () =>
      unwrap(
        api.GET("/api/v1/customers", { params: { query: { q: q || null, cursor: pager.cursor, limit: 50 } } }),
      ),
  });
  return (
    <>
      <MasterDataNav />
      <div className="flex flex-col gap-6">
        {can("masterdata.write") && (
          <Card title="Add a customer">
            <CreateCustomer />
          </Card>
        )}
        <Card title="Customers">
          <div className="mb-4 max-w-sm">
            <Field
              label="Search"
              type="search"
              hint="Name or code"
              value={q}
              onChange={(e) => {
                setQ(e.target.value);
                pager.reset();
              }}
            />
          </div>
          <DataTable
            caption="Customers"
            loading={list.isPending}
            error={list.error}
            rows={list.data?.data}
            empty={q ? "No customer matches." : "No customers yet. Import a customer file or add one."}
            columns={[
              {
                header: "Name",
                cell: (c) => (
                  <Link to={`/master-data/customers/${c.id}`} className="text-brand underline">
                    {c.name}
                  </Link>
                ),
              },
              { header: "Code", cell: (c) => c.code ?? "—" },
              { header: "Type", cell: (c) => TYPES.find(([v]) => v === c.customer_type)?.[1] ?? c.customer_type },
              { header: "Status", cell: (c) => (c.active ? "Active" : "Inactive") },
            ]}
          />
          <PagerControls pager={pager} page={list.data?.page} />
        </Card>
      </div>
    </>
  );
}

function CustomerForm({ c }: { c: Customer }) {
  const client = useQueryClient();
  const { can } = useSession();
  const [form, setForm] = useState({
    name: c.name,
    code: c.code ?? "",
    external_id: c.external_id ?? "",
    customer_type: c.customer_type as CustomerType,
    active: c.active,
    parent: c.parent_customer_id ?? "",
  });
  const save = useMutation({
    mutationFn: () =>
      unwrap(
        api.PATCH("/api/v1/customers/{customer_id}", {
          params: { path: { customer_id: c.id } },
          body: {
            name: form.name,
            code: orNull(form.code),
            external_id: orNull(form.external_id),
            customer_type: form.customer_type,
            active: form.active,
            parent_customer_id: orNull(form.parent),
          },
        }),
      ),
    onSuccess: () => void client.invalidateQueries({ queryKey: ["customers"] }),
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
        <FormGrid>
          <Field
            label="Name"
            value={form.name}
            error={fieldError(error, "name")}
            onChange={(e) => setForm({ ...form, name: e.target.value })}
          />
          <Field
            label="Code"
            value={form.code}
            error={fieldError(error, "code")}
            onChange={(e) => setForm({ ...form, code: e.target.value })}
          />
          <Field
            label="External ID"
            value={form.external_id}
            onChange={(e) => setForm({ ...form, external_id: e.target.value })}
          />
          <TypeSelect value={form.customer_type} onChange={(v) => setForm({ ...form, customer_type: v })} />
        </FormGrid>
        {c.parent_customer_id && (
          <p className="text-sm">
            Current parent:{" "}
            <Link to={`/master-data/customers/${c.parent_customer_id}`} className="text-brand underline">
              open parent customer
            </Link>
          </p>
        )}
        <EntityPicker
          kind="customer"
          label="Parent customer"
          value={form.parent}
          onChange={(v) => setForm({ ...form, parent: v })}
        />
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

function AddCustomerAlias({ customerId }: { customerId: string }) {
  const client = useQueryClient();
  const [alias, setAlias] = useState("");
  const add = useMutation({
    mutationFn: () =>
      unwrap(
        api.POST("/api/v1/customers/{customer_id}/aliases", {
          params: { path: { customer_id: customerId } },
          body: { alias, source: "manual" },
        }),
      ),
    onSuccess: () => {
      setAlias("");
      void client.invalidateQueries({ queryKey: ["customers", customerId] });
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
        hint="A name or code this customer appears under in your files"
        value={alias}
        error={fieldError(error, "alias")}
        onChange={(e) => setAlias(e.target.value)}
      />
      <Button type="submit" disabled={add.isPending}>
        Add alias
      </Button>
      {!fieldError(error, "alias") && <ErrorAlert error={error} />}
    </form>
  );
}

export function CustomerDetailPage() {
  const { customerId = "" } = useParams();
  const { can } = useSession();
  const c = useQuery({
    queryKey: ["customers", customerId],
    queryFn: () =>
      unwrap(api.GET("/api/v1/customers/{customer_id}", { params: { path: { customer_id: customerId } } })),
  });
  return (
    <>
      <MasterDataNav />
      <div className="flex flex-col gap-6">
        <Card title={c.data?.name ?? "Customer"}>
          <ErrorAlert error={c.error as ApiError | null} />
          {/* key: re-mount the form with fresh values when another customer is opened */}
          {c.isPending ? <Loading /> : c.data && <CustomerForm key={c.data.id} c={c.data} />}
        </Card>
        <Card title="Aliases">
          <DataTable
            caption="Customer aliases"
            loading={c.isPending}
            error={null}
            rows={c.data?.aliases}
            empty="No aliases."
            columns={[
              { header: "Alias", cell: (a) => a.alias },
              { header: "Source", cell: (a) => a.source ?? "—" },
              { header: "Added (UTC)", cell: (a) => utc(a.created_at) },
            ]}
          />
          {can("masterdata.write") && <AddCustomerAlias customerId={customerId} />}
        </Card>
        <Link to="/master-data/customers" className="text-brand underline">
          Back to customers
        </Link>
      </div>
    </>
  );
}
