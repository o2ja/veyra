import type { ReactNode } from "react";
import { Navigate, useLocation } from "react-router-dom";

import { Loading, PermissionDenied } from "../components/ui";
import { useSession } from "../lib/session";

/** Signed-in pages. Where to go is decided by the server's view of the session (/auth/me). */
export function RequireSession({
  children,
  allowEnrollment = false,
}: {
  children: ReactNode;
  allowEnrollment?: boolean;
}) {
  const { me, loading, expired } = useSession();
  const location = useLocation();
  if (loading) return <Loading />;
  const next = encodeURIComponent(location.pathname);
  if (!me) return <Navigate to={`/login?${expired ? "reason=expired&" : ""}next=${next}`} replace />;
  if (me.mfa_pending) return <Navigate to={`/login/mfa?next=${next}`} replace />;
  if (me.enrollment_required && !allowEnrollment) return <Navigate to="/mfa/setup" replace />;
  return <>{children}</>;
}

/** Hides a page the role cannot use. The server refuses the underlying calls regardless. */
export function RequirePermission({ permission, children }: { permission: string; children: ReactNode }) {
  const { can } = useSession();
  return can(permission) ? <>{children}</> : <PermissionDenied />;
}
