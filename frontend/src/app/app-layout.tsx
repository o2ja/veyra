import * as Menu from "@radix-ui/react-dropdown-menu";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { NavLink, Outlet, useNavigate } from "react-router-dom";

import { Logo } from "../components/ui";
import { api } from "../lib/api/client";
import { unwrap } from "../lib/api/errors";
import { useSession } from "../lib/session";

const NAV_GROUPS = [
  {
    title: "Overview",
    items: [
      { to: "/", label: "Home", permission: null },
      { to: "/dashboard", label: "Dashboard", permission: "data.read" },
    ],
  },
  {
    title: "Your data",
    items: [
      { to: "/files", label: "Files", permission: "data.read" },
      { to: "/imports", label: "Imports", permission: "data.read" },
      { to: "/lines/sales", label: "Lines", permission: "data.read" },
      { to: "/master-data", label: "Master data", permission: "data.read" },
    ],
  },
  {
    title: "Rebate contracts",
    items: [
      { to: "/agreements", label: "Agreements", permission: "data.read" },
      { to: "/extractions", label: "Extractions", permission: "data.read" },
      { to: "/calculations", label: "Calculations", permission: "data.read" },
    ],
  },
  {
    title: "Get money back",
    items: [
      { to: "/claims", label: "Claims", permission: "data.read" },
      { to: "/reconciliation", label: "Reconciliation", permission: "data.read" },
      { to: "/recovery", label: "Recovery", permission: "data.read" },
      { to: "/notifications", label: "Notifications", permission: "data.read" },
      { to: "/reports/audit-pack", label: "Audit pack", permission: "data.read" },
    ],
  },
  {
    title: "Administration",
    items: [
      { to: "/admin/users", label: "Users", permission: "user.manage" },
      { to: "/admin/invitations", label: "Invitations", permission: "invitation.manage" },
      { to: "/admin/settings", label: "Settings", permission: "settings.read" },
      { to: "/audit", label: "Audit log", permission: "audit.read" },
      { to: "/admin/jobs", label: "Jobs", permission: "jobs.manage" },
    ],
  },
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
      <Menu.Trigger className="max-w-48 truncate rounded-lg border border-line bg-panel px-3 py-1.5 text-sm shadow-card hover:bg-brand-soft">
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
      <Menu.Trigger className="hidden max-w-56 truncate rounded-lg px-2 py-1.5 text-sm text-muted hover:bg-brand-soft hover:text-ink sm:block">
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
  const [open, setOpen] = useState(false);
  const signedIn = Boolean(me && !me.mfa_pending && !me.enrollment_required);
  const groups = NAV_GROUPS.map((g) => ({
    ...g,
    items: g.items.filter((n) => n.permission === null || can(n.permission)),
  })).filter((g) => g.items.length > 0);

  return (
    <div className="min-h-dvh lg:flex">
      {signedIn && (
        <aside
          className={`${open ? "flex" : "hidden"} fixed inset-y-0 left-0 z-30 w-64 shrink-0 flex-col
            border-r border-line bg-panel text-ink lg:sticky lg:top-0 lg:flex lg:h-dvh`}
        >
          <NavLink
            to="/"
            className="flex h-16 items-center gap-2.5 border-b border-line px-5"
            aria-label="Veyra Recover home"
          >
            <Logo className="h-5" />
            <span className="text-xs font-medium tracking-[0.18em] text-muted uppercase">Recover</span>
          </NavLink>
          <nav aria-label="Main" className="flex-1 overflow-y-auto px-3 pb-6 text-sm">
            {groups.map((g) => (
              <div key={g.title} className="mt-5">
                <p className="px-2.5 pb-1.5 text-[11px] font-semibold tracking-wider text-muted uppercase">
                  {g.title}
                </p>
                <ul className="flex flex-col gap-0.5">
                  {g.items.map((n) => (
                    <li key={n.to}>
                      <NavLink
                        to={n.to}
                        end={n.to === "/"}
                        onClick={() => setOpen(false)}
                        className={({ isActive }) =>
                          `block rounded-lg px-2.5 py-1.5 ${
                            isActive
                              ? "bg-brand font-medium text-panel"
                              : "text-muted hover:bg-brand-soft hover:text-ink"
                          }`
                        }
                      >
                        {n.label}
                      </NavLink>
                    </li>
                  ))}
                </ul>
              </div>
            ))}
          </nav>
        </aside>
      )}
      {signedIn && open && (
        <button
          type="button"
          aria-label="Close menu"
          className="fixed inset-0 z-20 bg-black/40 lg:hidden"
          onClick={() => setOpen(false)}
        />
      )}
      <div className="flex min-w-0 flex-1 flex-col">
        <header className="sticky top-0 z-10 border-b border-line bg-panel/90 backdrop-blur">
          <div className="flex h-16 items-center gap-3 px-4 sm:px-8">
            {signedIn && (
              <button
                type="button"
                aria-label="Open menu"
                aria-expanded={open}
                className="grid size-9 place-items-center rounded-lg border border-line lg:hidden"
                onClick={() => setOpen((v) => !v)}
              >
                <span aria-hidden="true">☰</span>
              </button>
            )}
            <div className="ml-auto flex items-center gap-3">
              {signedIn && <WorkspaceSwitcher />}
              <UserMenu />
            </div>
          </div>
        </header>
        <main className="mx-auto w-full max-w-7xl flex-1 px-4 py-6 sm:px-8 sm:py-8">
          <Outlet />
        </main>
      </div>
    </div>
  );
}
