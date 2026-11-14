import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { useStepUp } from "../../components/step-up";
import { Card, Empty, ErrorAlert, Loading, PermissionDenied } from "../../components/ui";
import { api } from "../../lib/api/client";
import { ApiError, unwrap } from "../../lib/api/errors";
import { useSession } from "../../lib/session";

const ROLES = ["admin", "approver", "analyst", "viewer"] as const;
type Role = (typeof ROLES)[number];

export function UsersPage() {
  const client = useQueryClient();
  const stepUp = useStepUp();
  const { me } = useSession();
  const users = useQuery({ queryKey: ["users"], queryFn: () => unwrap(api.GET("/api/v1/users")) });
  const change = useMutation({
    mutationFn: (v: { user_id: string; role?: Role; status?: "active" | "disabled" }) =>
      stepUp(() =>
        unwrap(
          api.PATCH("/api/v1/users/{user_id}", {
            params: { path: { user_id: v.user_id } },
            body: { role: v.role ?? null, status: v.status ?? null },
          }),
        ),
      ),
    onSettled: () => client.invalidateQueries({ queryKey: ["users"] }),
  });

  if ((users.error as ApiError)?.status === 403) return <PermissionDenied />;

  return (
    <Card title="Users">
      <ErrorAlert error={(change.error as ApiError | null) ?? (users.error as ApiError | null)} />
      {users.isPending ? (
        <Loading />
      ) : !users.data?.data.length ? (
        <Empty>No members yet.</Empty>
      ) : (
        <table className="mt-2 w-full text-left text-sm">
          <thead className="text-muted">
            <tr>
              <th className="py-2">Name</th>
              <th>Email</th>
              <th>Role</th>
              <th>2FA</th>
              <th>Status</th>
            </tr>
          </thead>
          <tbody>
            {users.data.data.map((u) => {
              const self = u.user_id === me?.user.id;
              return (
                <tr key={u.user_id} className="border-t border-line">
                  <td className="py-2">{u.full_name}</td>
                  <td>{u.email}</td>
                  <td>
                    <label className="sr-only" htmlFor={`role-${u.user_id}`}>
                      Role of {u.full_name}
                    </label>
                    <select
                      id={`role-${u.user_id}`}
                      className="rounded border border-line bg-panel px-2 py-1"
                      value={u.role}
                      disabled={change.isPending}
                      onChange={(e) => change.mutate({ user_id: u.user_id, role: e.target.value as Role })}
                    >
                      {ROLES.map((r) => (
                        <option key={r} value={r}>
                          {r}
                        </option>
                      ))}
                    </select>
                  </td>
                  <td>{u.mfa_enabled ? "On" : "Off"}</td>
                  <td>
                    <button
                      type="button"
                      className="text-brand underline disabled:opacity-50"
                      disabled={change.isPending}
                      onClick={() =>
                        change.mutate({
                          user_id: u.user_id,
                          status: u.status === "active" ? "disabled" : "active",
                        })
                      }
                    >
                      {u.status === "active" ? (self ? "Deactivate (you)" : "Deactivate") : "Reactivate"}
                    </button>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}
    </Card>
  );
}
