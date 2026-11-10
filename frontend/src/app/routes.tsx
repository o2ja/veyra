import type { RouteObject } from "react-router-dom";

import { AuditPage } from "../features/admin/audit-page";
import { JobsPage } from "../features/admin/jobs-page";
import { InvitationsPage } from "../features/admin/invitations-page";
import { SettingsPage } from "../features/admin/settings-page";
import { UsersPage } from "../features/admin/users-page";
import {
  AcceptInvitePage,
  ConfirmEmailPage,
  ForgotPasswordPage,
  ResetPasswordPage,
  VerifyEmailPage,
} from "../features/auth/link-pages";
import { LoginPage, MfaChallengePage } from "../features/auth/login-pages";
import { MfaSetupPage } from "../features/auth/mfa-setup-page";
import { RegisterPage } from "../features/auth/register-page";
import { SecurityPage } from "../features/auth/security-page";
import { FileDetailPage } from "../features/files/file-detail-page";
import { FilesPage } from "../features/files/files-page";
import { HomePage } from "../features/home/home-page";
import { AppLayout } from "./app-layout";
import { RequirePermission, RequireSession } from "./guards";
import { RouteError } from "./route-error";

export const routes: RouteObject[] = [
  {
    element: <AppLayout />,
    errorElement: <RouteError />,
    children: [
      // public
      { path: "login", element: <LoginPage /> },
      { path: "login/mfa", element: <MfaChallengePage /> },
      { path: "register", element: <RegisterPage /> },
      { path: "verify-email", element: <VerifyEmailPage /> },
      { path: "forgot-password", element: <ForgotPasswordPage /> },
      { path: "reset-password", element: <ResetPasswordPage /> },
      { path: "accept-invite", element: <AcceptInvitePage /> },
      { path: "confirm-email", element: <ConfirmEmailPage /> },
      // signed in
      {
        path: "mfa/setup",
        element: (
          <RequireSession allowEnrollment>
            <MfaSetupPage />
          </RequireSession>
        ),
      },
      {
        index: true,
        element: (
          <RequireSession>
            <HomePage />
          </RequireSession>
        ),
      },
      {
        path: "account/security",
        element: (
          <RequireSession>
            <SecurityPage />
          </RequireSession>
        ),
      },
      {
        path: "admin/users",
        element: (
          <RequireSession>
            <RequirePermission permission="user.manage">
              <UsersPage />
            </RequirePermission>
          </RequireSession>
        ),
      },
      {
        path: "admin/invitations",
        element: (
          <RequireSession>
            <RequirePermission permission="invitation.manage">
              <InvitationsPage />
            </RequirePermission>
          </RequireSession>
        ),
      },
      {
        path: "admin/settings",
        element: (
          <RequireSession>
            <RequirePermission permission="settings.read">
              <SettingsPage />
            </RequirePermission>
          </RequireSession>
        ),
      },
      {
        path: "files",
        element: (
          <RequireSession>
            <RequirePermission permission="data.read">
              <FilesPage />
            </RequirePermission>
          </RequireSession>
        ),
      },
      {
        path: "files/:fileId",
        element: (
          <RequireSession>
            <RequirePermission permission="data.read">
              <FileDetailPage />
            </RequirePermission>
          </RequireSession>
        ),
      },
      {
        path: "admin/jobs",
        element: (
          <RequireSession>
            <RequirePermission permission="jobs.manage">
              <JobsPage />
            </RequirePermission>
          </RequireSession>
        ),
      },
      {
        path: "audit",
        element: (
          <RequireSession>
            <RequirePermission permission="audit.read">
              <AuditPage />
            </RequirePermission>
          </RequireSession>
        ),
      },
    ],
  },
];
