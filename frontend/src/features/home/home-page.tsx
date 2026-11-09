import { useEffect, useState } from "react";

import { api } from "../../lib/api/client";

type ApiState = "checking" | "live" | "unreachable";

export function HomePage() {
  const [state, setState] = useState<ApiState>("checking");

  useEffect(() => {
    let cancelled = false;
    api
      .GET("/api/v1/health/live")
      .then(({ data }) => !cancelled && setState(data?.status === "ok" ? "live" : "unreachable"))
      .catch(() => !cancelled && setState("unreachable"));
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <section aria-labelledby="home-title" className="rounded-lg border border-line bg-panel p-6">
      <h1 id="home-title" className="text-lg font-semibold">
        Workspace
      </h1>
      <p className="mt-1 text-muted">Sign-in and workspaces arrive in the next stage.</p>
      <p className="mt-4 text-sm" role="status" aria-live="polite">
        API:{" "}
        <span
          className={state === "live" ? "text-ok" : state === "unreachable" ? "text-danger" : "text-muted"}
        >
          {state}
        </span>
      </p>
    </section>
  );
}
