import { useQuery } from "@tanstack/react-query";

import { Card, Empty, ErrorAlert, Loading, PermissionDenied } from "../../components/ui";
import { api } from "../../lib/api/client";
import { ApiError, unwrap } from "../../lib/api/errors";
import { ExtractionsNav } from "./common";

export function AiUsagePage() {
  const usage = useQuery({
    queryKey: ["ai", "usage"],
    queryFn: () => unwrap(api.GET("/api/v1/ai/usage")),
  });

  const err = usage.error as ApiError | null;

  if (err?.status === 403 || err?.code === "forbidden") {
    return (
      <>
        <ExtractionsNav />
        <PermissionDenied />
      </>
    );
  }

  return (
    <>
      <ExtractionsNav />
      <div className="flex flex-col gap-6">
        <Card title="AI usage">
          <ErrorAlert error={err} />
          {usage.isPending ? (
            <Loading />
          ) : (
            usage.data && (
              <dl className="grid gap-3 text-sm sm:grid-cols-2 lg:grid-cols-4">
                <div>
                  <dt className="text-muted">Status</dt>
                  <dd className="font-medium">{usage.data.enabled ? "Enabled" : "Disabled"}</dd>
                </div>
                <div>
                  <dt className="text-muted">Model</dt>
                  <dd className="font-mono text-xs">{usage.data.model}</dd>
                </div>
                <div>
                  <dt className="text-muted">Today&apos;s tokens</dt>
                  <dd className="font-mono">
                    {usage.data.today.tokens} / {usage.data.today.token_cap}
                  </dd>
                </div>
                <div>
                  <dt className="text-muted">Today&apos;s cost (USD)</dt>
                  <dd className="font-mono">
                    {usage.data.today.cost_usd} / {usage.data.today.cost_cap_usd}
                  </dd>
                </div>
              </dl>
            )
          )}
        </Card>

        {usage.data && (
          <Card title="Daily usage">
            {usage.data.daily.length === 0 ? (
              <Empty>No daily AI usage recorded.</Empty>
            ) : (
              <div className="overflow-x-auto">
                <table className="w-full text-left text-sm">
                  <caption className="sr-only">Daily AI usage</caption>
                  <thead className="text-muted">
                    <tr>
                      <th scope="col" className="py-2 pr-4 font-medium">
                        Date
                      </th>
                      <th scope="col" className="py-2 pr-4 text-right font-medium">
                        Calls
                      </th>
                      <th scope="col" className="py-2 pr-4 text-right font-medium">
                        Input tokens
                      </th>
                      <th scope="col" className="py-2 pr-4 text-right font-medium">
                        Output tokens
                      </th>
                      <th scope="col" className="py-2 pr-4 text-right font-medium">
                        Cost (USD)
                      </th>
                    </tr>
                  </thead>
                  <tbody>
                    {usage.data.daily.map((d) => (
                      <tr key={d.date} className="border-t border-line">
                        <td className="py-2 pr-4">{d.date}</td>
                        <td className="py-2 pr-4 text-right font-mono">{d.calls}</td>
                        <td className="py-2 pr-4 text-right font-mono">{d.input_tokens}</td>
                        <td className="py-2 pr-4 text-right font-mono">{d.output_tokens}</td>
                        <td className="py-2 pr-4 text-right font-mono">{d.cost_usd}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </Card>
        )}
      </div>
    </>
  );
}
