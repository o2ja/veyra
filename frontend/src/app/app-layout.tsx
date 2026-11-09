import * as Menu from "@radix-ui/react-dropdown-menu";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { NavLink, Outlet, useNavigate } from "react-router-dom";

import { api } from "../lib/api/client";
import { unwrap } from "../lib/api/errors";
import { useSession } from "../lib/session";

const NAV = [
  { to: "/", label: "Home", permission: null },
  { to: "/admin/users", label: "Users", permission: "user.manage" },
  { to: "/admin/invitations", label: "Invitations", permission: "invitation.manage" },
  { to: "/admin/settings", label: "Settings", permission: "settings.read" },
  { to: "/audit", label: "Audit log", permission: "audit.read" },
] as const;

function WorkspaceSwitcher() {
  const { me, refresh } = useSession();
  const navigate = useNavigate();
  const workspaces = useQuery({
    queryKey: ["auth", "tenants"],
    queryFn: () => unwrap(api.GET("/api/v1/auth/tenants")),
    enabled: Boolean(me),
  });
  const list = workspaces.data?.data ?? [];
  if (!me?.tenant) return null;

  async function switchTo(tenantId: string) {
    await unwrap(api.POST("/api/v1/auth/switch-tenant", { body: { tenant_id: tenantId } }));
    await refresh();
    navigate("/");
  }

  return (
    <Menu.Root>
      <Menu.Trigger className="rounded-md border border-line px-3 py-1.5 text-sm">
        <span className="sr-only">Switch workspace: </span>
        {me.tenant.name} <span aria-hidden="true">▾</span>
      </Menu.Trigger>
      <Menu.Portal>
        <Menu.Content
          className="min-w-56 rounded-md border border-line bg-panel p-1 shadow-lg"
          sideOffset={4}
        >
          <Menu.Label className="px-2 py-1 text-xs text-muted">Workspaces</Menu.Label>
          {list.map((w) => (
            <Menu.Item
              key={w.tenant_id}
              className="cursor-pointer rounded px-2 py-1.5 text-sm outline-none data-[highlighted]:bg-surface"
              disabled={w.tenant_id === me.tenant?.id}
              onSelect={() => void switchTo(w.tenant_id)}
            >
              {w.name} <span className="text-muted">· {w.role}</span>
            </Menu.Item>
          ))}
        </Menu.Content>
      </Menu.Portal>
    </Menu.Root>
  );
}

function UserMenu() {
  const { me } = useSession();
  const client = useQueryClient();
  const navigate = useNavigate();
  if (!me) return null;

  async function logout() {
    await api.POST("/api/v1/auth/logout");
    client.clear();
    navigate("/login");
  }

  return (
    <Menu.Root>
      <Menu.Trigger className="text-sm text-muted underline-offset-2 hover:underline">
        {me.user.email}
      </Menu.Trigger>
      <Menu.Portal>
        <Menu.Content
          className="min-w-44 rounded-md border border-line bg-panel p-1 shadow-lg"
          sideOffset={4}
          align="end"
        >
          <Menu.Item
            className="cursor-pointer rounded px-2 py-1.5 text-sm outline-none data-[highlighted]:bg-surface"
            onSelect={() => navigate("/account/security")}
          >
            Security
          </Menu.Item>
          <Menu.Item
            className="cursor-pointer rounded px-2 py-1.5 text-sm outline-none data-[highlighted]:bg-surface"
            onSelect={() => void logout()}
          >
            Sign out
          </Menu.Item>
        </Menu.Content>
      </Menu.Portal>
    </Menu.Root>
  );
}

export function AppLayout() {
  const { me, can } = useSession();
  const signedIn = Boolean(me && !me.mfa_pending && !me.enrollment_required);
  return (
    <div className="flex min-h-dvh flex-col">
      <header className="border-b border-line bg-panel">
        <div className="mx-auto flex h-14 max-w-6xl items-center gap-6 px-4">
          <span className="font-semibold tracking-tight">Veyra Recover</span>
          {signedIn && (
            <nav aria-label="Main" className="flex gap-4 text-sm">
              {NAV.filter((n) => n.permission === null || can(n.permission)).map((n) => (
                <NavLink
                  key={n.to}
                  to={n.to}
                  end={n.to === "/"}
                  className={({ isActive }) =>
                    isActive ? "font-medium text-ink" : "text-muted hover:text-ink"
                  }
                >
                  {n.label}
                </NavLink>
              ))}
            </nav>
          )}
          <div className="ml-auto flex items-center gap-4">
            {signedIn && <WorkspaceSwitcher />}
            <UserMenu />
          </div>
        </div>
      </header>
      <main className="mx-auto w-full max-w-6xl flex-1 px-4 py-8">
        <Outlet />
      </main>
    </div>
  );
}
