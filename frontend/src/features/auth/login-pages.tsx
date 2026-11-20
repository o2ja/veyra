import { zodResolver } from "@hookform/resolvers/zod";
import { useForm } from "react-hook-form";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { z } from "zod";

import { Alert, AuthCard, Button, ErrorAlert, Field } from "../../components/ui";
import { api } from "../../lib/api/client";
import { unwrap } from "../../lib/api/errors";
import { useSession } from "../../lib/session";
import { useApiSubmit } from "./use-api-form";

const loginSchema = z.object({
  email: z.string().trim().email("Enter a valid email address."),
  password: z.string().min(1, "Enter your password."),
});

const REASONS: Record<string, string> = {
  expired: "Your session has ended. Sign in again to continue.",
  reset: "Your password was changed. Sign in with the new password.",
  verified: "Your email is confirmed. Sign in to set up two-factor authentication.",
  email: "Your sign-in email was changed. Sign in with the new address.",
  invited: "Invitation accepted. Sign in to continue.",
};

export function LoginPage() {
  const [params] = useSearchParams();
  const navigate = useNavigate();
  const { refresh } = useSession();
  const { register, handleSubmit, formState } = useForm<z.infer<typeof loginSchema>>({
    resolver: zodResolver(loginSchema),
  });
  const { busy, error, submit } = useApiSubmit();
  const reason = REASONS[params.get("reason") ?? ""];
  const next = params.get("next");

  const onSubmit = handleSubmit(async (values) => {
    const result = await submit(() => unwrap(api.POST("/api/v1/auth/login", { body: values })));
    if (!result) return;
    await refresh();
    const target = next && next.startsWith("/") && !next.startsWith("//") ? next : "/";
    navigate(result.status === "mfa_required" ? `/login/mfa?next=${encodeURIComponent(target)}` : target);
  });

  return (
    <AuthCard title="Sign in">
      <form className="flex flex-col gap-4" onSubmit={onSubmit} noValidate>
        {reason && <Alert tone="info">{reason}</Alert>}
        <ErrorAlert error={error} />
        <Field
          label="Email"
          type="email"
          autoComplete="username"
          {...register("email")}
          error={formState.errors.email?.message}
        />
        <Field
          label="Password"
          type="password"
          autoComplete="current-password"
          {...register("password")}
          error={formState.errors.password?.message}
        />
        <Button type="submit" disabled={busy}>
          Sign in
        </Button>
        <div className="flex justify-between text-sm">
          <Link className="text-brand underline" to="/forgot-password">
            Forgot password?
          </Link>
          <Link className="text-brand underline" to="/register">
            Create a workspace
          </Link>
        </div>
      </form>
    </AuthCard>
  );
}

export function MfaChallengePage() {
  const [params] = useSearchParams();
  const navigate = useNavigate();
  const { refresh } = useSession();
  const { register, handleSubmit } = useForm<{ code: string; trust: boolean }>();
  const { busy, error, submit } = useApiSubmit();

  const onSubmit = handleSubmit(async ({ code, trust }) => {
    const ok = await submit(() =>
      unwrap(api.POST("/api/v1/auth/mfa/verify", { body: { code: code.trim(), trust_device: trust } })),
    );
    if (!ok) return;
    await refresh();
    const next = params.get("next");
    navigate(next && next.startsWith("/") && !next.startsWith("//") ? next : "/");
  });

  return (
    <AuthCard title="Two-factor authentication">
      <form className="flex flex-col gap-4" onSubmit={onSubmit}>
        <p className="text-sm text-muted">
          Enter the 6-digit code from your authenticator app, or one of your recovery codes.
        </p>
        <ErrorAlert error={error} />
        <Field
          label="Code"
          autoComplete="one-time-code"
          inputMode="text"
          autoFocus
          {...register("code", { required: true })}
        />
        <label className="flex items-center gap-2 text-sm">
          <input type="checkbox" {...register("trust")} />
          Trust this device for 30 days
        </label>
        <Button type="submit" disabled={busy}>
          Verify
        </Button>
      </form>
    </AuthCard>
  );
}
