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
  MasterDataNav,
  PagerControls,
  SelectField,
  orNull,
  usePager,
} from "./common";
import { REASON_CODES, REASON_CODE_LABELS, type ReasonCode } from "./reason-codes";

type Settings = components["schemas"]["ManufacturerSettingsOut"];
type SettingsIn = components["schemas"]["ManufacturerSettingsIn"];
type MappingIn = components["schemas"]["ReasonCodeMappingIn"];
type Mapping = Omit<MappingIn, "reason_code"> & { reason_code: ReasonCode | "" }; // "" = not chosen yet

function CreateManufacturer() {
  const client = useQueryClient();
  const [form, setForm] = useState({ name: "", code: "", external_id: "" });
  const create = useMutation({
    mutationFn: () =>
      unwrap(
        api.POST("/api/v1/manufacturers", {
          body: { name: form.name, code: form.code, external_id: orNull(form.external_id), active: true },
        }),
      ),
    onSuccess: () => {
      setForm({ name: "", code: "", external_id: "" });
      void client.invalidateQueries({ queryKey: ["manufacturers"] });
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
          required
          hint="Short code, for example VOLT"
          value={form.code}
          error={fieldError(error, "code")}
          onChange={(e) => setForm({ ...form, code: e.target.value })}
        />
        <Field
          label="External ID"
          value={form.external_id}
          onChange={(e) => setForm({ ...form, external_id: e.target.value })}
        />
      </FormGrid>
      {create.isSuccess && <Alert tone="ok">Manufacturer added.</Alert>}
      <ErrorAlert error={error} />
      <div>
        <Button type="submit" disabled={create.isPending}>
          Add manufacturer
        </Button>
      </div>
    </form>
  );
}

export function ManufacturersPage() {
  const { can } = useSession();
  const pager = usePager();
  const list = useQuery({
    queryKey: ["manufacturers", pager.cursor],
    queryFn: () =>
      unwrap(api.GET("/api/v1/manufacturers", { params: { query: { cursor: pager.cursor, limit: 50 } } })),
  });
  return (
    <>
      <MasterDataNav />
      <div className="flex flex-col gap-6">
        {can("masterdata.write") && (
          <Card title="Add a manufacturer">
            <CreateManufacturer />
          </Card>
        )}
        <Card title="Manufacturers">
          <DataTable
            caption="Manufacturers"
            loading={list.isPending}
            error={list.error}
            rows={list.data?.data}
            empty="No manufacturers yet."
            columns={[
              {
                header: "Name",
                cell: (m) => (
                  <Link to={`/master-data/manufacturers/${m.id}`} className="text-brand underline">
                    {m.name}
                  </Link>
                ),
              },
              { header: "Code", cell: (m) => m.code },
              { header: "External ID", cell: (m) => m.external_id ?? "—" },
              { header: "Status", cell: (m) => (m.active ? "Active" : "Inactive") },
            ]}
          />
          <PagerControls pager={pager} page={list.data?.page} />
        </Card>
      </div>
    </>
  );
}

function SettingsForm({ manufacturerId, initial }: { manufacturerId: string; initial: Settings }) {
  const client = useQueryClient();
  const { can } = useSession();
  const editable = can("manufacturer_settings.write");
  const [form, setForm] = useState(initial);
  const save = useMutation({
    mutationFn: () =>
      unwrap(
        api.PUT("/api/v1/manufacturers/{manufacturer_id}/settings", {
          params: {
            path: { manufacturer_id: manufacturerId },
            header: { "if-match": `"${form.revision}"` }, // optimistic concurrency (API §4)
          },
          body: {
            claim_window_days: form.claim_window_days,
            claim_window_basis: form.claim_window_basis as NonNullable<
              SettingsIn["claim_window_basis"]
            > | null,
            dispute_window_days: form.dispute_window_days,
            dispute_window_basis: form.dispute_window_basis as NonNullable<
              SettingsIn["dispute_window_basis"]
            > | null,
            variance_tolerance: form.variance_tolerance,
            conflict_policy: form.conflict_policy as NonNullable<SettingsIn["conflict_policy"]>,
            allow_negative_claims: form.allow_negative_claims,
            payment_lag_days_expected: form.payment_lag_days_expected,
            notes: form.notes,
          },
        }),
      ),
    onSuccess: (data) => {
      setForm(data);
      client.setQueryData(["manufacturers", manufacturerId, "settings"], data);
    },
  });
  const error = save.error as ApiError | null;
  const days = (v: string) => (v === "" ? null : Number(v));

  async function reload() {
    const fresh = await unwrap(
      api.GET("/api/v1/manufacturers/{manufacturer_id}/settings", {
        params: { path: { manufacturer_id: manufacturerId } },
      }),
    );
    setForm(fresh);
    save.reset();
  }

  return (
    <form
      className="flex flex-col gap-3"
      onSubmit={(e) => {
        e.preventDefault();
        save.mutate();
      }}
    >
      {!editable && <Alert tone="info">Only admins and approvers can change these settings.</Alert>}
      <fieldset disabled={!editable} className="flex flex-col gap-3">
        <FormGrid>
          <Field
            label="Claim window (days)"
            type="number"
            min={1}
            value={form.claim_window_days ?? ""}
            error={fieldError(error, "claim_window_days")}
            onChange={(e) => setForm({ ...form, claim_window_days: days(e.target.value) })}
          />
          <SelectField
            label="Claim window starts at"
            value={form.claim_window_basis ?? ""}
            onChange={(e) => setForm({ ...form, claim_window_basis: orNull(e.target.value) })}
          >
            <option value="">Not set</option>
            <option value="invoice_date">Invoice date</option>
            <option value="ship_date">Ship date</option>
            <option value="period_end">Period end</option>
          </SelectField>
          <Field
            label="Dispute window (days)"
            type="number"
            min={1}
            value={form.dispute_window_days ?? ""}
            error={fieldError(error, "dispute_window_days")}
            onChange={(e) => setForm({ ...form, dispute_window_days: days(e.target.value) })}
          />
          <SelectField
            label="Dispute window starts at"
            value={form.dispute_window_basis ?? ""}
            onChange={(e) => setForm({ ...form, dispute_window_basis: orNull(e.target.value) })}
          >
            <option value="">Not set</option>
            <option value="remittance_date">Remittance date</option>
            <option value="submission_date">Submission date</option>
          </SelectField>
          <Field
            label="Variance tolerance"
            inputMode="decimal"
            hint="Amount, for example 0.01"
            value={form.variance_tolerance}
            error={fieldError(error, "variance_tolerance")}
            onChange={(e) => setForm({ ...form, variance_tolerance: e.target.value.trim() })}
          />
          <SelectField
            label="Conflicting agreements"
            value={form.conflict_policy}
            onChange={(e) => setForm({ ...form, conflict_policy: e.target.value })}
          >
            <option value="flag">Flag for review</option>
            <option value="priority">Use priority order</option>
          </SelectField>
          <Field
            label="Expected payment lag (days)"
            type="number"
            min={0}
            value={form.payment_lag_days_expected ?? ""}
            onChange={(e) => setForm({ ...form, payment_lag_days_expected: days(e.target.value) })}
          />
          <Field
            label="Notes"
            value={form.notes ?? ""}
            onChange={(e) => setForm({ ...form, notes: orNull(e.target.value) })}
          />
        </FormGrid>
        <Checkbox
          label="Allow negative claims"
          checked={form.allow_negative_claims}
          onChange={(v) => setForm({ ...form, allow_negative_claims: v })}
        />
      </fieldset>
      {save.isSuccess && <Alert tone="ok">Settings saved.</Alert>}
      {error?.code === "precondition_failed" ? (
        <Alert>
          Someone else changed these settings.{" "}
          <button type="button" className="underline" onClick={() => void reload()}>
            Reload
          </button>{" "}
          and try again.
        </Alert>
      ) : (
        <ErrorAlert error={error} />
      )}
      {editable && (
        <div>
          <Button type="submit" disabled={save.isPending}>
            Save settings
          </Button>
        </div>
      )}
    </form>
  );
}

function ReasonCodeMappings({ manufacturerId }: { manufacturerId: string }) {
  const { can } = useSession();
  const editable = can("masterdata.write");
  const [rows, setRows] = useState<Mapping[] | null>(null);
  const query = useQuery({
    queryKey: ["manufacturers", manufacturerId, "reason-codes"],
    queryFn: () =>
      unwrap(
        api.GET("/api/v1/manufacturers/{manufacturer_id}/reason-code-mappings", {
          params: { path: { manufacturer_id: manufacturerId } },
        }),
      ),
  });
  const current: Mapping[] =
    rows ??
    query.data?.data.map((m) => ({
      external_code: m.external_code,
      external_text: m.external_text,
      reason_code: m.reason_code,
    })) ??
    [];
  const save = useMutation({
    mutationFn: () =>
      unwrap(
        api.PUT("/api/v1/manufacturers/{manufacturer_id}/reason-code-mappings", {
          params: { path: { manufacturer_id: manufacturerId } },
          body: { mappings: current as MappingIn[] }, // save is disabled while any row is ""
        }),
      ),
    onSuccess: (data) => {
      setRows(null);
      query.refetch();
      return data;
    },
  });
  const update = (i: number, patch: Partial<Mapping>) =>
    setRows(current.map((r, j) => (j === i ? { ...r, ...patch } : r)));

  if (query.isPending) return <Loading />;
  return (
    <form
      className="flex flex-col gap-3"
      onSubmit={(e) => {
        e.preventDefault();
        save.mutate();
      }}
    >
      <p className="text-sm text-muted">
        Translate the manufacturer&apos;s own deduction or rejection codes into Veyra reason codes (for
        example <code>rejected_price_mismatch</code>). Saving replaces the whole list.
      </p>
      <ErrorAlert error={(query.error as ApiError | null) ?? (save.error as ApiError | null)} />
      {current.length === 0 && <p className="text-sm text-muted">No mappings yet.</p>}
      {current.map((r, i) => (
        <fieldset
          key={i}
          disabled={!editable}
          className="grid gap-2 sm:grid-cols-[1fr_2fr_1fr_auto] sm:items-end"
        >
          <legend className="sr-only">Mapping {i + 1}</legend>
          <Field
            label="Manufacturer code"
            value={r.external_code}
            error={fieldError(save.error as ApiError | null, `mappings.${i}.external_code`)}
            onChange={(e) => update(i, { external_code: e.target.value })}
          />
          <Field
            label="Manufacturer text"
            value={r.external_text ?? ""}
            onChange={(e) => update(i, { external_text: orNull(e.target.value) })}
          />
          <SelectField
            label="Reason code"
            value={r.reason_code}
            onChange={(e) => update(i, { reason_code: e.target.value as ReasonCode | "" })}
          >
            <option value="">Choose…</option>
            {REASON_CODES.map((c) => (
              <option key={c} value={c}>
                {REASON_CODE_LABELS[c]}
              </option>
            ))}
          </SelectField>
          {editable && (
            <Button
              variant="secondary"
              aria-label={`Remove mapping ${i + 1}`}
              onClick={() => setRows(current.filter((_, j) => j !== i))}
            >
              Remove
            </Button>
          )}
        </fieldset>
      ))}
      {save.isSuccess && rows === null && <Alert tone="ok">Mappings saved.</Alert>}
      {editable && (
        <div className="flex gap-3">
          <Button
            variant="secondary"
            onClick={() => setRows([...current, { external_code: "", external_text: null, reason_code: "" }])}
          >
            Add mapping
          </Button>
          <Button
            type="submit"
            disabled={save.isPending || rows === null || current.some((r) => !r.reason_code)}
          >
            Save mappings
          </Button>
        </div>
      )}
    </form>
  );
}

function ManufacturerForm({ m }: { m: components["schemas"]["ManufacturerOut"] }) {
  const client = useQueryClient();
  const { can } = useSession();
  const [form, setForm] = useState({ name: m.name, external_id: m.external_id ?? "", active: m.active });
  const save = useMutation({
    mutationFn: () =>
      unwrap(
        api.PATCH("/api/v1/manufacturers/{manufacturer_id}", {
          params: { path: { manufacturer_id: m.id } },
          body: { name: form.name, external_id: orNull(form.external_id), active: form.active },
        }),
      ),
    onSuccess: () => void client.invalidateQueries({ queryKey: ["manufacturers"] }),
  });
  const error = save.error as ApiError | null;
  return (
    <form
      className="flex flex-col gap-3"
      onSubmit={(e) => {
        e.preventDefault();
        save.mutate();
      }}
    >
      <fieldset disabled={!can("masterdata.write")} className="flex flex-col gap-3">
        <FormGrid>
          <Field
            label="Name"
            value={form.name}
            error={fieldError(error, "name")}
            onChange={(e) => setForm({ ...form, name: e.target.value })}
          />
          <Field label="Code" value={m.code} disabled hint="The code cannot be changed." />
          <Field
            label="External ID"
            value={form.external_id}
            onChange={(e) => setForm({ ...form, external_id: e.target.value })}
          />
        </FormGrid>
        <Checkbox label="Active" checked={form.active} onChange={(v) => setForm({ ...form, active: v })} />
      </fieldset>
      {save.isSuccess && <Alert tone="ok">Saved.</Alert>}
      <ErrorAlert error={error} />
      {can("masterdata.write") && (
        <div>
          <Button type="submit" disabled={save.isPending}>
            Save
          </Button>
        </div>
      )}
    </form>
  );
}

export function ManufacturerDetailPage() {
  const { manufacturerId = "" } = useParams();
  const path = { manufacturer_id: manufacturerId };
  const m = useQuery({
    queryKey: ["manufacturers", manufacturerId],
    queryFn: () => unwrap(api.GET("/api/v1/manufacturers/{manufacturer_id}", { params: { path } })),
  });
  const settings = useQuery({
    queryKey: ["manufacturers", manufacturerId, "settings"],
    queryFn: () => unwrap(api.GET("/api/v1/manufacturers/{manufacturer_id}/settings", { params: { path } })),
  });
  return (
    <>
      <MasterDataNav />
      <div className="flex flex-col gap-6">
        <Card title={m.data?.name ?? "Manufacturer"}>
          <ErrorAlert error={m.error as ApiError | null} />
          {m.isPending ? <Loading /> : m.data && <ManufacturerForm m={m.data} />}
        </Card>
        <Card title="Claim and dispute settings">
          <ErrorAlert error={settings.error as ApiError | null} />
          {settings.isPending ? (
            <Loading />
          ) : (
            settings.data && <SettingsForm manufacturerId={manufacturerId} initial={settings.data} />
          )}
        </Card>
        <Card title="Reason-code mappings">
          <ReasonCodeMappings manufacturerId={manufacturerId} />
        </Card>
        <Link to="/master-data/manufacturers" className="text-brand underline">
          Back to manufacturers
        </Link>
      </div>
    </>
  );
}
