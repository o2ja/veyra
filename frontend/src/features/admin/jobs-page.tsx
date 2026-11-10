import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { Card, Empty, ErrorAlert, Loading } from "../../components/ui";
import { api } from "../../lib/api/client";
import { ApiError, unwrap } from "../../lib/api/errors";

const KIND_TEXT: Record<string, string> = {
  files_scan: "Malware scan",
  files_notify_blocked: "Blocked-file email",
  files_purge_quarantine: "Quarantine clean-up",
  identity_registration_email: "Verification email",
  identity_password_reset: "Password reset email",
  identity_email_change: "Email change confirmation",
  tenancy_invitation_email: "Invitation email",
};

/** Failed (dead-letter) background jobs of this workspace, with retry (admin, `jobs.manage`). */
export function JobsPage() {
  const client = useQueryClient();
  const failed = useQuery({
    queryKey: ["jobs", "failed"],
    queryFn: () => unwrap(api.GET("/api/v1/jobs", { params: { query: { status: "failed" } } })),
  });
  const retry = useMutation({
    mutationFn: (id: string) =>
      unwrap(api.POST("/api/v1/jobs/{job_id}/retry", { params: { path: { job_id: id } } })),
    onSettled: () => {
      void client.invalidateQueries({ queryKey: ["jobs"] });
      void client.invalidateQueries({ queryKey: ["files"] });
    },
  });

  return (
    <Card title="Failed background jobs">
      <ErrorAlert error={(failed.error as ApiError | null) ?? (retry.error as ApiError | null)} />
      {failed.isPending ? (
        <Loading />
      ) : !failed.data?.data.length ? (
        <Empty>No failed jobs.</Empty>
      ) : (
        <table className="w-full text-left text-sm">
          <thead className="text-muted">
            <tr>
              <th className="py-2">Job</th>
              <th>Attempts</th>
              <th>Last error</th>
              <th>Failed at (UTC)</th>
              <th>
                <span className="sr-only">Actions</span>
              </th>
            </tr>
          </thead>
          <tbody>
            {failed.data.data.map((j) => (
              <tr key={j.id} className="border-t border-line">
                <td className="py-2">{KIND_TEXT[j.kind] ?? j.kind}</td>
                <td>
                  {j.attempts} / {j.max_attempts}
                </td>
                <td className="font-mono text-xs">{j.last_error}</td>
                <td>{j.finished_at?.replace("T", " ").slice(0, 19)}</td>
                <td>
                  <button
                    type="button"
                    className="text-brand underline disabled:opacity-50"
                    disabled={retry.isPending}
                    onClick={() => retry.mutate(j.id)}
                  >
                    Retry
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </Card>
  );
}
