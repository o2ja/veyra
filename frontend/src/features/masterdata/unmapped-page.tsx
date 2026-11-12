import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router-dom";

import type { components } from "../../api/schema";
import { Alert, Button, Card, ErrorAlert, PermissionDenied } from "../../components/ui";
import { api } from "../../lib/api/client";
import { ApiError, unwrap } from "../../lib/api/errors";
import { DataTable, EntityPicker, MasterDataNav, PagerControls, SelectField, usePager, utc } from "./common";

type Entry = components["schemas"]["UnmappedOut"];
type Status = "open" | "resolved" | "ignored";
type Kind = "customer" | "product";

function Resolve({ entry, onDone }: { entry: Entry; onDone: () => void }) {
  const [target, setTarget] = useState("");
  const resolve = useMutation({
    mutationFn: (body: components["schemas"]["ResolveIn"]) =>
      unwrap(
        api.POST("/api/v1/unmapped-entities/{entry_id}/resolve", {
          params: { path: { entry_id: entry.id } },
          body,
        }),
      ),
    onSuccess: onDone,
  });
  return (
    <div className="flex flex-col gap-2">
      <EntityPicker
        kind={entry.entity_type as Kind}
        label={`Map “${entry.external_value}” to`}
        value={target}
        onChange={setTarget}
      />
      <div className="flex gap-2">
        <Button
          disabled={!target || resolve.isPending}
          onClick={() => resolve.mutate({ action: "map", target_id: target })}
        >
          Map
        </Button>
        <Button
          variant="secondary"
          disabled={resolve.isPending}
          onClick={() => resolve.mutate({ action: "ignore" })}
        >
          Ignore
        </Button>
      </div>
      <ErrorAlert error={resolve.error as ApiError | null} />
    </div>
  );
}

/**
 * Customers and products seen in imports without a master-data match. Mapping adds an alias on the
 * chosen record; re-import the file afterwards to load the held rows.
 */
export function UnmappedPage() {
  const client = useQueryClient();
  const pager = usePager();
  const [status, setStatus] = useState<Status>("open");
  const [kind, setKind] = useState<Kind | "">("");
  const [done, setDone] = useState(false);
  const list = useQuery({
    queryKey: ["unmapped", status, kind, pager.cursor],
    queryFn: () =>
      unwrap(
        api.GET("/api/v1/unmapped-entities", {
          params: { query: { status, entity_type: kind || null, cursor: pager.cursor, limit: 50 } },
        }),
      ),
  });
  if ((list.error as ApiError)?.status === 403) {
    return (
      <>
        <MasterDataNav />
        <PermissionDenied />
      </>
    );
  }

  return (
    <>
      <MasterDataNav />
      <Card title="Unmapped customers and products">
        <p className="mb-4 text-sm text-muted">
          Values from imported files that match no customer or product. Rows that use them were held back; map
          each value to a record (or ignore it), then import the file again to load those rows.
        </p>
        <div className="mb-4 grid max-w-xl gap-3 sm:grid-cols-2">
          <SelectField
            label="Status"
            value={status}
            onChange={(e) => {
              setStatus(e.target.value as Status);
              pager.reset();
            }}
          >
            <option value="open">Open</option>
            <option value="resolved">Mapped</option>
            <option value="ignored">Ignored</option>
          </SelectField>
          <SelectField
            label="Kind"
            value={kind}
            onChange={(e) => {
              setKind(e.target.value as Kind | "");
              pager.reset();
            }}
          >
            <option value="">Customers and products</option>
            <option value="customer">Customers</option>
            <option value="product">Products</option>
          </SelectField>
        </div>
        {done && <Alert tone="ok">Done. Import the file again to load the held rows.</Alert>}
        <DataTable
          caption="Unmapped values"
          loading={list.isPending}
          error={list.error}
          rows={list.data?.data}
          empty={status === "open" ? "Nothing to review." : "No entries."}
          columns={[
            { header: "Kind", cell: (e) => (e.entity_type === "customer" ? "Customer" : "Product") },
            { header: "Value in file", cell: (e) => <span className="font-mono">{e.external_value}</span> },
            { header: "Example name", cell: (e) => e.example_name ?? "—" },
            { header: "Seen", cell: (e) => `${e.seen_count}×, last ${utc(e.last_seen_at)}` },
            {
              header: "First import",
              cell: (e) =>
                e.first_import_batch_id ? (
                  <Link to={`/imports/${e.first_import_batch_id}`} className="text-brand underline">
                    Open import
                  </Link>
                ) : (
                  "—"
                ),
            },
            {
              header: status === "open" ? "Resolve" : "Resolved",
              cell: (e) =>
                e.status === "open" ? (
                  <Resolve
                    entry={e}
                    onDone={() => {
                      setDone(true);
                      void client.invalidateQueries({ queryKey: ["unmapped"] });
                    }}
                  />
                ) : (
                  `${e.status === "resolved" ? "Mapped" : "Ignored"} ${utc(e.resolved_at)}`
                ),
            },
          ]}
        />
        <PagerControls pager={pager} page={list.data?.page} />
      </Card>
    </>
  );
}
