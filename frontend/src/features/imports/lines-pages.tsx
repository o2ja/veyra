import { useQuery } from "@tanstack/react-query";
import { Link, NavLink, useParams, useSearchParams } from "react-router-dom";

import type { components } from "../../api/schema";
import { Card, ErrorAlert, Field, Loading } from "../../components/ui";
import { api } from "../../lib/api/client";
import { ApiError, unwrap } from "../../lib/api/errors";
import {
  Checkbox,
  DataTable,
  EntityPicker,
  ManufacturerSelect,
  PagerControls,
  SelectField,
  usePager,
} from "../masterdata/common";

type Lineage = components["schemas"]["Lineage"];
type Kind = "sales" | "purchases";

/** Where a line came from: the uploaded file and the row in it, and the import batch. */
function LineageCell({ l }: { l: Lineage }) {
  return (
    <span className="text-xs">
      <Link to={`/files/${l.file_id}`} className="text-brand underline">
        File, row {l.source_row_number}
      </Link>
      {" · "}
      <Link to={`/imports/${l.import_batch_id}`} className="text-brand underline">
        import
      </Link>
      {" · "}v{l.version}
      {!l.is_current && " (superseded)"}
    </span>
  );
}

function LinesNav() {
  return (
    <nav aria-label="Line type" className="mb-6 flex gap-4 text-sm">
      {(["sales", "purchases"] as const).map((k) => (
        <NavLink
          key={k}
          to={`/lines/${k}`}
          className={({ isActive }) =>
            isActive ? "font-medium underline underline-offset-4" : "text-muted hover:text-ink"
          }
        >
          {k === "sales" ? "Sales lines" : "Purchase lines"}
        </NavLink>
      ))}
    </nav>
  );
}

/** Browse normalised source lines with filters and keyset pagination; filters live in the URL. */
export function LinesPage({ kind }: { kind: Kind }) {
  const [params, setParams] = useSearchParams();
  const pager = usePager();
  const get = (k: string) => params.get(k) ?? "";
  const set = (k: string, v: string) => {
    const next = new URLSearchParams(params);
    if (v) next.set(k, v);
    else next.delete(k);
    setParams(next, { replace: true });
    pager.reset();
  };
  const filters = {
    manufacturer_id: get("manufacturer_id") || null,
    product_id: get("product_id") || null,
    import_batch_id: get("import_batch_id") || null,
    document_number: get("document_number") || null,
    date_from: get("date_from") || null,
    date_to: get("date_to") || null,
    current: get("all_versions") ? null : true,
    cursor: pager.cursor,
    limit: 50,
  };
  const docType = get("doc_type");
  const sales = useQuery({
    queryKey: ["lines", "sales", params.toString(), pager.cursor],
    queryFn: () =>
      unwrap(
        api.GET("/api/v1/sales-lines", {
          params: {
            query: {
              ...filters,
              customer_id: get("customer_id") || null,
              doc_type: (docType || null) as "invoice" | "credit_note" | null,
            },
          },
        }),
      ),
    enabled: kind === "sales",
  });
  const purchases = useQuery({
    queryKey: ["lines", "purchases", params.toString(), pager.cursor],
    queryFn: () =>
      unwrap(
        api.GET("/api/v1/purchase-lines", {
          params: {
            query: { ...filters, doc_type: (docType || null) as "receipt" | "vendor_credit" | null },
          },
        }),
      ),
    enabled: kind === "purchases",
  });
  const query = kind === "sales" ? sales : purchases;

  return (
    <>
      <LinesNav />
      <Card title={kind === "sales" ? "Sales lines" : "Purchase lines"}>
        <form className="mb-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-3" onSubmit={(e) => e.preventDefault()}>
          <ManufacturerSelect
            allowAny="All manufacturers"
            value={get("manufacturer_id")}
            onChange={(v) => set("manufacturer_id", v)}
          />
          <SelectField
            label="Document type"
            value={docType}
            onChange={(e) => set("doc_type", e.target.value)}
          >
            <option value="">All</option>
            {kind === "sales" ? (
              <>
                <option value="invoice">Invoices</option>
                <option value="credit_note">Credit notes</option>
              </>
            ) : (
              <>
                <option value="receipt">Receipts</option>
                <option value="vendor_credit">Vendor credits</option>
              </>
            )}
          </SelectField>
          <Field
            label="Document number"
            type="search"
            value={get("document_number")}
            onChange={(e) => set("document_number", e.target.value.trim())}
          />
          <Field
            label="Date from"
            type="date"
            value={get("date_from")}
            onChange={(e) => set("date_from", e.target.value)}
          />
          <Field
            label="Date to"
            type="date"
            value={get("date_to")}
            onChange={(e) => set("date_to", e.target.value)}
          />
          <Field
            label="Import batch ID"
            value={get("import_batch_id")}
            onChange={(e) => set("import_batch_id", e.target.value.trim())}
          />
          {kind === "sales" && (
            <EntityPicker
              kind="customer"
              label="Customer"
              value={get("customer_id")}
              onChange={(v) => set("customer_id", v)}
            />
          )}
          <EntityPicker
            kind="product"
            label="Product"
            value={get("product_id")}
            onChange={(v) => set("product_id", v)}
          />
          <Checkbox
            label="Include superseded versions"
            checked={Boolean(get("all_versions"))}
            onChange={(v) => set("all_versions", v ? "1" : "")}
          />
        </form>
        {kind === "sales" ? (
          <DataTable
            caption="Sales lines"
            loading={sales.isPending}
            error={sales.error}
            rows={sales.data?.data}
            empty="No lines match."
            columns={[
              {
                header: "Document",
                cell: (l) => (
                  <Link to={`/lines/sales/${l.id}`} className="text-brand underline">
                    {l.document_number} / {l.line_number}
                  </Link>
                ),
              },
              { header: "Type", cell: (l) => (l.doc_type === "credit_note" ? "Credit note" : "Invoice") },
              { header: "Invoice date", cell: (l) => l.invoice_date },
              { header: "SKU", cell: (l) => <span className="font-mono">{l.sku_raw}</span> },
              {
                header: "Quantity",
                cell: (l) => (
                  <span className="font-mono">
                    {l.quantity} {l.uom}
                  </span>
                ),
              },
              { header: "Unit price", cell: (l) => <span className="font-mono">{l.unit_price}</span> },
              {
                header: "Amount",
                cell: (l) => (
                  <span className="font-mono">
                    {l.extended_amount} {l.currency_code}
                  </span>
                ),
              },
              { header: "Source", cell: (l) => <LineageCell l={l.lineage} /> },
            ]}
          />
        ) : (
          <DataTable
            caption="Purchase lines"
            loading={purchases.isPending}
            error={purchases.error}
            rows={purchases.data?.data}
            empty="No lines match."
            columns={[
              { header: "Document", cell: (l) => `${l.document_number} / ${l.line_number}` },
              { header: "Type", cell: (l) => (l.doc_type === "vendor_credit" ? "Vendor credit" : "Receipt") },
              { header: "Date", cell: (l) => l.document_date },
              { header: "SKU", cell: (l) => <span className="font-mono">{l.sku_raw}</span> },
              {
                header: "Quantity",
                cell: (l) => (
                  <span className="font-mono">
                    {l.quantity} {l.uom}
                  </span>
                ),
              },
              { header: "Unit cost", cell: (l) => <span className="font-mono">{l.unit_cost}</span> },
              {
                header: "Amount",
                cell: (l) => (
                  <span className="font-mono">
                    {l.extended_amount} {l.currency_code}
                  </span>
                ),
              },
              { header: "Source", cell: (l) => <LineageCell l={l.lineage} /> },
            ]}
          />
        )}
        <PagerControls pager={pager} page={query.data?.page} />
      </Card>
    </>
  );
}

export function SalesLinePage() {
  const { lineId = "" } = useParams();
  const line = useQuery({
    queryKey: ["lines", "sales", lineId],
    queryFn: () =>
      unwrap(api.GET("/api/v1/sales-lines/{line_id}", { params: { path: { line_id: lineId } } })),
  });
  const l = line.data;
  const rows: [string, React.ReactNode][] = l
    ? [
        ["Type", l.doc_type === "credit_note" ? "Credit note" : "Invoice"],
        ["Invoice date", l.invoice_date],
        ["Ship date", l.ship_date ?? "—"],
        [
          "Customer",
          <Link key="c" to={`/master-data/customers/${l.customer_id}`} className="text-brand underline">
            Open customer
          </Link>,
        ],
        [
          "SKU in file",
          <span key="s" className="font-mono">
            {l.sku_raw}
          </span>,
        ],
        [
          "Product",
          l.product_id ? (
            <Link key="p" to={`/master-data/products/${l.product_id}`} className="text-brand underline">
              Open product
            </Link>
          ) : (
            "Not matched"
          ),
        ],
        ["Description", l.description ?? "—"],
        ["Quantity", `${l.quantity} ${l.uom}`],
        ["Unit price", l.unit_price],
        ["Unit cost", l.unit_cost ?? "—"],
        ["Replacement cost", l.replacement_cost ?? "—"],
        ["Amount", `${l.extended_amount} ${l.currency_code}`],
        ["SPA reference", l.spa_reference ?? "—"],
        ["Project reference", l.project_reference ?? "—"],
        [
          "Original line",
          l.original_line_id ? (
            <Link key="o" to={`/lines/sales/${l.original_line_id}`} className="text-brand underline">
              Open original invoice line
            </Link>
          ) : (
            "—"
          ),
        ],
        ["Source", <LineageCell key="src" l={l.lineage} />],
        ["Source system", l.lineage.source_system],
        [
          "Previous version",
          l.previous_version_id ? (
            <Link key="pv" to={`/lines/sales/${l.previous_version_id}`} className="text-brand underline">
              Open previous version
            </Link>
          ) : (
            "—"
          ),
        ],
        [
          "Newer version",
          l.lineage.superseded_by_id ? (
            <Link key="nv" to={`/lines/sales/${l.lineage.superseded_by_id}`} className="text-brand underline">
              Open newer version
            </Link>
          ) : (
            "—"
          ),
        ],
        ["Changed after claim", l.source_changed ? "Yes" : "No"],
      ]
    : [];
  return (
    <Card title={l ? `Sales line ${l.document_number} / ${l.line_number}` : "Sales line"}>
      <ErrorAlert error={line.error as ApiError | null} />
      {line.isPending && <Loading />}
      {l && (
        <dl className="grid grid-cols-[max-content_1fr] gap-x-6 gap-y-2 text-sm">
          {rows.map(([k, v]) => (
            <div key={k} className="contents">
              <dt className="text-muted">{k}</dt>
              <dd>{v}</dd>
            </div>
          ))}
        </dl>
      )}
      <Link to="/lines/sales" className="mt-4 inline-block text-brand underline">
        Back to sales lines
      </Link>
    </Card>
  );
}
