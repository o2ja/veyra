import type { ReactNode } from "react";
import { Navigate, type RouteObject } from "react-router-dom";

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
import { ImportDetailPage } from "../features/imports/import-detail-page";
import { ImportsPage } from "../features/imports/imports-page";
import { LinesPage, SalesLinePage } from "../features/imports/lines-pages";
import { NewImportPage } from "../features/imports/new-import-page";
import { BranchesPage } from "../features/masterdata/branches-page";
import { CustomerDetailPage, CustomersPage } from "../features/masterdata/customers-pages";
import { ManufacturerDetailPage, ManufacturersPage } from "../features/masterdata/manufacturers-pages";
import { ProductDetailPage, ProductsPage } from "../features/masterdata/products-pages";
import { UnmappedPage } from "../features/masterdata/unmapped-page";
import { UomPage } from "../features/masterdata/uom-page";
import { AgreementsPage } from "../features/agreements/agreements-page";
import { AppLayout } from "./app-layout";
import { RequirePermission, RequireSession } from "./guards";
import { RouteError } from "./route-error";

/** A signed-in page that needs one permission (the server enforces it on every call regardless). */
const guarded = (path: string, permission: string, page: ReactNode): RouteObject => ({
  path,
  element: (
    <RequireSession>
      <RequirePermission permission={permission}>{page}</RequirePermission>
    </RequireSession>
  ),
});

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
      // S3: imports, source lines, master data
      guarded("imports", "data.read", <ImportsPage />),
      guarded("imports/new", "import.run", <NewImportPage />),
      guarded("imports/:importId", "data.read", <ImportDetailPage />),
      guarded("lines/sales", "data.read", <LinesPage key="sales" kind="sales" />),
      guarded("lines/sales/:lineId", "data.read", <SalesLinePage />),
      guarded("lines/purchases", "data.read", <LinesPage key="purchases" kind="purchases" />),
      { path: "master-data", element: <Navigate to="/master-data/manufacturers" replace /> },
      guarded("master-data/manufacturers", "data.read", <ManufacturersPage />),
      guarded("master-data/manufacturers/:manufacturerId", "data.read", <ManufacturerDetailPage />),
      guarded("master-data/customers", "data.read", <CustomersPage />),
      guarded("master-data/customers/:customerId", "data.read", <CustomerDetailPage />),
      guarded("master-data/products", "data.read", <ProductsPage />),
      guarded("master-data/products/:productId", "data.read", <ProductDetailPage />),
      guarded("master-data/uom", "data.read", <UomPage />),
      guarded("master-data/branches", "data.read", <BranchesPage />),
      guarded("master-data/unmapped", "masterdata.write", <UnmappedPage />),
      // S4: agreements and drafts
      guarded("agreements", "data.read", <AgreementsPage />),
    ],
  },
];
