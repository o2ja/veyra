import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { useStepUp } from "../../components/step-up";
import { Alert, Button, Card, Empty, ErrorAlert, Field, Loading, fieldError } from "../../components/ui";
import { api } from "../../lib/api/client";
import { ApiError, unwrap } from "../../lib/api/errors";

type Role = "admin" | "approver" | "analyst" | "viewer";

function state(i: { accepted_at: string | null; revoked_at: string | null; expires_at: string }): string {
  if (i.accepted_at) return "Accepted";
  if (i.revoked_at) return "Revoked";
  return new Date(i.expires_at) < new Date() ? "Expired" : "Open";
}

export function InvitationsPage() {
  const client = useQueryClient();
  const stepUp = useStepUp();
  const [email, setEmail] = useState("");
  const [role, setRole] = useState<Role>("analyst");
  const [sent, setSent] = useState<string | null>(null);
  const list = useQuery({ queryKey: ["invitations"], queryFn: () => unwrap(api.GET("/api/v1/invitations")) });
  const create = useMutation({
    mutationFn: () => stepUp(() => unwrap(api.POST("/api/v1/invitations", { body: { email, role } }))),
    onSuccess: (inv) => {
      setSent(inv.email);
      setEmail("");
    },
    onSettled: () => client.invalidateQueries({ queryKey: ["invitations"] }),
  });
  const revoke = useMutation({
    mutationFn: (id: string) =>
      unwrap(api.DELETE("/api/v1/invitations/{invitation_id}", { params: { path: { invitation_id: id } } })),
    onSettled: () => client.invalidateQueries({ queryKey: ["invitations"] }),
  });
  const createError = create.error as ApiError | null;

  return (
    <div className="flex flex-col gap-6">
      <Card title="Invite someone">
        <form
          className="flex max-w-xl flex-col gap-3 sm:flex-row sm:items-end"
          onSubmit={(e) => {
            e.preventDefault();
            setSent(null);
            create.mutate();
          }}
        >
          <div className="flex-1">
            <Field
              label="Email"
              type="email"
              value={email}
              onChange={(e) => setEmail(e.target.value.trim())}
              error={fieldError(createError, "email")}
              required
            />
          </div>
          <div className="flex flex-col gap-1">
            <label htmlFor="invite-role" className="text-sm font-medium">
              Role
            </label>
            <select
              id="invite-role"
              className="rounded-md border border-line bg-panel px-3 py-2 text-sm"
              value={role}
              onChange={(e) => setRole(e.target.value as Role)}
            >
              <option value="viewer">viewer (read only)</option>
              <option value="analyst">analyst</option>
              <option value="approver">approver</option>
              <option value="admin">admin</option>
            </select>
          </div>
          <Button type="submit" disabled={create.isPending}>
            Invite
          </Button>
        </form>
        <div className="mt-3 max-w-xl">
          {sent && <Alert tone="ok">Invitation created for {sent}. It expires in 7 days.</Alert>}
          <ErrorAlert error={createError?.details.length ? null : createError} />
        </div>
      </Card>
      <Card title="Invitations">
        <ErrorAlert error={(list.error as ApiError | null) ?? (revoke.error as ApiError | null)} />
        {list.isPending ? (
          <Loading />
        ) : !list.data?.data.length ? (
          <Empty>No invitations yet.</Empty>
        ) : (
          <table className="w-full text-left text-sm">
            <thead className="text-muted">
              <tr>
                <th className="py-2">Email</th>
                <th>Role</th>
                <th>Status</th>
                <th>Expires</th>
                <th>
                  <span className="sr-only">Actions</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {list.data.data.map((i) => (
                <tr key={i.id} className="border-t border-line">
                  <td className="py-2">{i.email}</td>
                  <td>{i.role}</td>
                  <td>{state(i)}</td>
                  <td>{new Date(i.expires_at).toLocaleDateString("en-US")}</td>
                  <td>
                    {state(i) === "Open" && (
                      <button
                        type="button"
                        className="text-brand underline"
                        onClick={() => revoke.mutate(i.id)}
                      >
                        Revoke
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>
    </div>
  );
}
