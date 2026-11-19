import { useInfiniteQuery } from "@tanstack/react-query";
import { useState } from "react";

import { Button, Card, Empty, ErrorAlert, Field, Loading, PermissionDenied } from "../../components/ui";
import { api } from "../../lib/api/client";
import { ApiError, unwrap } from "../../lib/api/errors";
import { formatDateTime } from "../../lib/format";

interface Filters {
  action: string;
  actor_user_id: string;
  entity_type: string;
  date_from: string;
  date_to: string;
}

const EMPTY: Filters = { action: "", actor_user_id: "", entity_type: "", date_from: "", date_to: "" };

function clean(f: Filters): Record<string, string> {
  return Object.fromEntries(Object.entries(f).filter(([, v]) => v !== ""));
}

function changes(before: Record<string, unknown> | null, after: Record<string, unknown> | null): string {
  const keys = new Set([...Object.keys(before ?? {}), ...Object.keys(after ?? {})]);
  return [...keys]
    .map((k) => `${k}: ${JSON.stringify(before?.[k] ?? "—")} → ${JSON.stringify(after?.[k] ?? "—")}`)
    .join("; ");
}

export function AuditPage() {
  const [draft, setDraft] = useState<Filters>(EMPTY);
  const [filters, setFilters] = useState<Filters>(EMPTY);
  const query = useInfiniteQuery({
    queryKey: ["audit", filters],
    initialPageParam: null as string | null,
    queryFn: ({ pageParam }) =>
      unwrap(
        api.GET("/api/v1/audit-log", {
          params: { query: { ...clean(filters), limit: 50, ...(pageParam ? { cursor: pageParam } : {}) } },
        }),
      ),
    getNextPageParam: (last) => last.page.next_cursor,
  });
  const rows = query.data?.pages.flatMap((p) => p.data) ?? [];

  if ((query.error as ApiError)?.status === 403) return <PermissionDenied />;

  return (
    <Card title="Audit log">
      <form
        className="mb-4 grid grid-cols-1 gap-3 sm:grid-cols-5"
        onSubmit={(e) => {
          e.preventDefault();
          setFilters(draft);
        }}
      >
        <Field
          label="Action"
          placeholder="e.g. settings.updated"
          value={draft.action}
          onChange={(e) => setDraft({ ...draft, action: e.target.value.trim() })}
        />
        <Field
          label="Entity type"
          placeholder="e.g. membership"
          value={draft.entity_type}
          onChange={(e) => setDraft({ ...draft, entity_type: e.target.value.trim() })}
        />
        <Field
          label="Actor user id"
          value={draft.actor_user_id}
          onChange={(e) => setDraft({ ...draft, actor_user_id: e.target.value.trim() })}
        />
        <Field
          label="From"
          type="date"
          value={draft.date_from}
          onChange={(e) => setDraft({ ...draft, date_from: e.target.value })}
        />
        <Field
          label="To"
          type="date"
          value={draft.date_to}
          onChange={(e) => setDraft({ ...draft, date_to: e.target.value })}
        />
        <div className="sm:col-span-5">
          <Button type="submit">Apply filters</Button>
        </div>
      </form>
      <ErrorAlert error={query.error as ApiError | null} />
      {query.isPending ? (
        <Loading />
      ) : rows.length === 0 ? (
        <Empty>No entries match.</Empty>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-left text-sm">
            <thead className="text-muted">
              <tr>
                <th className="py-2">Time</th>
                <th>Action</th>
                <th>Actor</th>
                <th>Entity</th>
                <th>Change</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.id} className="border-t border-line align-top">
                  <td className="py-2 whitespace-nowrap">{formatDateTime(r.occurred_at)}</td>
                  <td className="font-mono">{r.action}</td>
                  <td className="text-xs">
                    {r.actor_name ? (
                      <>
                        {r.actor_name}
                        <span className="block text-muted">{r.actor_email}</span>
                      </>
                    ) : (
                      r.actor_type
                    )}
                  </td>
                  <td>{r.entity_type}</td>
                  <td className="text-xs">{changes(r.before, r.after)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {query.hasNextPage && (
        <Button
          className="mt-4"
          variant="secondary"
          onClick={() => void query.fetchNextPage()}
          disabled={query.isFetchingNextPage}
        >
          Load more
        </Button>
      )}
    </Card>
  );
}
