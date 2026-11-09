import { zodResolver } from "@hookform/resolvers/zod";
import { useEffect, useRef, useState } from "react";
import { useForm } from "react-hook-form";
import { Link, useNavigate } from "react-router-dom";
import { z } from "zod";

import { Alert, AuthCard, Button, ErrorAlert, Field, Loading, fieldError } from "../../components/ui";
import { api } from "../../lib/api/client";
import { unwrap } from "../../lib/api/errors";
import { takeFragmentToken } from "../../lib/fragment-token";
import { useSession } from "../../lib/session";
import { useApiSubmit } from "./use-api-form";

function useToken(): string | null {
  const [token] = useState(takeFragmentToken);
  return token;
}

function InvalidLink() {
  return (
    <AuthCard title="Link not valid">
      <Alert>This link is incomplete, expired or already used. Ask for a new one.</Alert>
      <p className="mt-4 text-sm">
        <Link className="text-brand underline" to="/login">
          Go to sign in
        </Link>
      </p>
    </AuthCard>
  );
}

/** One-shot link pages (verify email, confirm email change): call once, then go to sign-in. */
function OneShot({
  title,
  run,
  done,
}: {
  title: string;
  run: (token: string) => Promise<unknown>;
  done: string;
}) {
  const token = useToken();
  const navigate = useNavigate();
  const { busy, error, submit } = useApiSubmit();
  const started = useRef(false);

  useEffect(() => {
    if (!token || started.current) return;
    started.current = true;
    void submit(() => run(token)).then((ok) => ok !== undefined && navigate(`/login?reason=${done}`));
  }, [token, run, submit, navigate, done]);

  if (!token) return <InvalidLink />;
  return (
    <AuthCard title={title}>
      {busy || !error ? <Loading label="Checking the link…" /> : <ErrorAlert error={error} />}
    </AuthCard>
  );
}

/** Verify needs the registration password too, so a link someone else triggered for your address
 * cannot create an account you did not register (S1 code review finding 8). */
export function VerifyEmailPage() {
  const token = useToken();
  const navigate = useNavigate();
  const [password, setPassword] = useState("");
  const { busy, error, submit } = useApiSubmit();
  if (!token) return <InvalidLink />;

  async function onSubmit(event: React.FormEvent) {
    event.preventDefault();
    const ok = await submit(() =>
      unwrap(api.POST("/api/v1/auth/verify-email", { body: { token: token as string, password } })),
    );
    if (ok) navigate("/login?reason=verified");
  }

  return (
    <AuthCard title="Confirm your email">
      <form className="flex flex-col gap-4" onSubmit={onSubmit}>
        <p className="text-sm text-muted">
          Enter the password you chose when you registered. If you did not register, close this page.
        </p>
        <ErrorAlert error={error} />
        <Field
          label="Password"
          type="password"
          autoComplete="current-password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          required
        />
        <Button type="submit" disabled={busy}>
          Confirm and create workspace
        </Button>
      </form>
    </AuthCard>
  );
}

export function ConfirmEmailPage() {
  return (
    <OneShot
      title="Confirming your new email"
      done="email"
      run={(token) => unwrap(api.POST("/api/v1/auth/email/confirm", { body: { token } }))}
    />
  );
}

const passwordSchema = z
  .object({
    new_password: z.string().min(12, "Use at least 12 characters.").max(256),
    confirm: z.string(),
  })
  .refine((v) => v.new_password === v.confirm, { path: ["confirm"], message: "The passwords do not match." });

export function ResetPasswordPage() {
  const token = useToken();
  const navigate = useNavigate();
  const { register, handleSubmit, formState } = useForm<z.infer<typeof passwordSchema>>({
    resolver: zodResolver(passwordSchema),
  });
  const { busy, error, submit } = useApiSubmit();
  if (!token) return <InvalidLink />;

  const onSubmit = handleSubmit(async ({ new_password }) => {
    const ok = await submit(() =>
      unwrap(api.POST("/api/v1/auth/password/reset", { body: { token, new_password } })),
    );
    if (ok) navigate("/login?reason=reset");
  });

  return (
    <AuthCard title="Choose a new password">
      <form className="flex flex-col gap-4" onSubmit={onSubmit} noValidate>
        <ErrorAlert error={error?.details.length ? null : error} />
        <Field
          label="New password"
          type="password"
          autoComplete="new-password"
          {...register("new_password")}
          error={formState.errors.new_password?.message ?? fieldError(error, "new_password")}
        />
        <Field
          label="Repeat new password"
          type="password"
          autoComplete="new-password"
          {...register("confirm")}
          error={formState.errors.confirm?.message}
        />
        <Button type="submit" disabled={busy}>
          Set password
        </Button>
        <p className="text-xs text-muted">
          All your sessions are signed out. Two-factor authentication stays on.
        </p>
      </form>
    </AuthCard>
  );
}

export function ForgotPasswordPage() {
  const { register, handleSubmit } = useForm<{ email: string }>();
  const { busy, error, submit } = useApiSubmit();
  const [message, setMessage] = useState<string | null>(null);

  const onSubmit = handleSubmit(async ({ email }) => {
    const result = await submit(() =>
      unwrap(api.POST("/api/v1/auth/password/forgot", { body: { email: email.trim() } })),
    );
    if (result) setMessage(result.message);
  });

  return (
    <AuthCard title="Reset your password">
      {message ? (
        <Alert tone="info">{message}</Alert>
      ) : (
        <form className="flex flex-col gap-4" onSubmit={onSubmit}>
          <ErrorAlert error={error} />
          <Field label="Email" type="email" autoComplete="email" {...register("email", { required: true })} />
          <Button type="submit" disabled={busy}>
            Send reset link
          </Button>
        </form>
      )}
    </AuthCard>
  );
}

const acceptSchema = z.object({
  full_name: z.string().trim().min(1, "Enter your name.").max(200),
  password: z.string().min(12, "Use at least 12 characters.").max(256),
});

export function AcceptInvitePage() {
  const token = useToken();
  const navigate = useNavigate();
  const { me, refresh } = useSession();
  const { register, handleSubmit, formState } = useForm<z.infer<typeof acceptSchema>>({
    resolver: zodResolver(acceptSchema),
  });
  const { busy, error, submit } = useApiSubmit();
  if (!token) return <InvalidLink />;

  async function acceptAsSignedIn() {
    const ok = await submit(() =>
      unwrap(api.POST("/api/v1/invitations/accept", { body: { token: token as string } })),
    );
    if (ok) {
      await refresh();
      navigate("/");
    }
  }

  const onSubmit = handleSubmit(async (values) => {
    const ok = await submit(() =>
      unwrap(api.POST("/api/v1/invitations/accept", { body: { token, ...values } })),
    );
    if (ok) navigate("/login?reason=invited");
  });

  if (me && !me.mfa_pending) {
    return (
      <AuthCard title="Join workspace">
        <p className="text-sm">
          You are signed in as <strong>{me.user.email}</strong>. Accept the invitation with this account?
        </p>
        <div className="mt-4 flex flex-col gap-3">
          <ErrorAlert error={error} />
          <Button onClick={acceptAsSignedIn} disabled={busy}>
            Accept invitation
          </Button>
        </div>
      </AuthCard>
    );
  }
  return (
    <AuthCard title="Join workspace">
      <form className="flex flex-col gap-4" onSubmit={onSubmit} noValidate>
        <p className="text-sm text-muted">
          New to Veyra Recover? Choose your name and password. Already have an account?{" "}
          <Link className="text-brand underline" to="/login">
            Sign in first
          </Link>
          , then open the link again.
        </p>
        <ErrorAlert error={error?.details.length ? null : error} />
        <Field
          label="Your name"
          autoComplete="name"
          {...register("full_name")}
          error={formState.errors.full_name?.message}
        />
        <Field
          label="Password"
          type="password"
          autoComplete="new-password"
          {...register("password")}
          error={formState.errors.password?.message ?? fieldError(error, "password")}
        />
        <Button type="submit" disabled={busy}>
          Accept invitation
        </Button>
      </form>
    </AuthCard>
  );
}
