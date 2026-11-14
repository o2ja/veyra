import { useState } from "react";
import { useNavigate } from "react-router-dom";

import { useStepUp } from "../../components/step-up";
import { Alert, Button, Card, ErrorAlert, Field, fieldError } from "../../components/ui";
import { api } from "../../lib/api/client";
import { unwrap } from "../../lib/api/errors";
import { useSession } from "../../lib/session";
import { useApiSubmit } from "./use-api-form";

/** Account security: password, recovery codes, sign-in email (step-up protected on the server). */
export function SecurityPage() {
  const { me } = useSession();
  return (
    <div className="flex flex-col gap-6">
      <ChangePassword />
      {me?.user.mfa_enabled && <RecoveryCodes />}
      <ChangeEmail />
    </div>
  );
}

function ChangePassword() {
  const stepUp = useStepUp();
  const { busy, error, submit } = useApiSubmit();
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [done, setDone] = useState(false);

  async function onSubmit(event: React.FormEvent) {
    event.preventDefault();
    const ok = await submit(() =>
      stepUp(() =>
        unwrap(
          api.POST("/api/v1/auth/password/change", {
            body: { current_password: current, new_password: next },
          }),
        ),
      ),
    );
    if (ok) {
      setDone(true);
      setCurrent("");
      setNext("");
    }
  }

  return (
    <Card title="Password">
      <form className="flex max-w-md flex-col gap-3" onSubmit={onSubmit}>
        {done && <Alert tone="ok">Password changed. Your other sessions were signed out.</Alert>}
        <ErrorAlert error={error?.details.length ? null : error} />
        <Field
          label="Current password"
          type="password"
          autoComplete="current-password"
          value={current}
          onChange={(e) => setCurrent(e.target.value)}
          required
        />
        <Field
          label="New password"
          type="password"
          autoComplete="new-password"
          value={next}
          onChange={(e) => setNext(e.target.value)}
          error={fieldError(error, "new_password")}
          required
        />
        <Button type="submit" disabled={busy}>
          Change password
        </Button>
      </form>
    </Card>
  );
}

function RecoveryCodes() {
  const stepUp = useStepUp();
  const { busy, error, submit } = useApiSubmit();
  const [codes, setCodes] = useState<string[] | null>(null);

  async function regenerate() {
    const result = await submit(() => stepUp(() => unwrap(api.POST("/api/v1/auth/mfa/recovery-codes"))));
    if (result) setCodes(result.recovery_codes);
  }

  return (
    <Card title="Recovery codes">
      <ErrorAlert error={error} />
      {codes ? (
        <>
          <Alert tone="info">
            New codes. The old ones no longer work. Save these now; they are shown once.
          </Alert>
          <ol className="mt-3 grid max-w-md grid-cols-2 gap-2 font-mono text-sm">
            {codes.map((c) => (
              <li key={c} className="rounded-lg border border-line px-2 py-1">
                {c}
              </li>
            ))}
          </ol>
        </>
      ) : (
        <Button variant="secondary" onClick={regenerate} disabled={busy}>
          Create new recovery codes
        </Button>
      )}
    </Card>
  );
}

function ChangeEmail() {
  const navigate = useNavigate();
  const { me } = useSession();
  const { busy, error, submit } = useApiSubmit();
  const [values, setValues] = useState({ new_email: "", password: "", mfa_code: "" });
  const [message, setMessage] = useState<string | null>(null);

  async function onSubmit(event: React.FormEvent) {
    event.preventDefault();
    const result = await submit(() => unwrap(api.POST("/api/v1/auth/email/change", { body: values })));
    if (result) setMessage(result.message);
  }

  if (!me?.user.mfa_enabled) {
    return (
      <Card title="Sign-in email">
        <p className="text-sm text-muted">Turn on two-factor authentication to change your sign-in email.</p>
        <Button className="mt-3" variant="secondary" onClick={() => navigate("/mfa/setup")}>
          Set up two-factor authentication
        </Button>
      </Card>
    );
  }
  return (
    <Card title="Sign-in email">
      <p className="mb-3 text-sm text-muted">Current: {me.user.email}</p>
      {message ? (
        <Alert tone="info">{message}</Alert>
      ) : (
        <form className="flex max-w-md flex-col gap-3" onSubmit={onSubmit}>
          <ErrorAlert error={error?.details.length ? null : error} />
          <Field
            label="New email"
            type="email"
            value={values.new_email}
            onChange={(e) => setValues({ ...values, new_email: e.target.value.trim() })}
            error={fieldError(error, "new_email")}
            required
          />
          <Field
            label="Password"
            type="password"
            autoComplete="current-password"
            value={values.password}
            onChange={(e) => setValues({ ...values, password: e.target.value })}
            required
          />
          <Field
            label="Authenticator code"
            inputMode="numeric"
            autoComplete="one-time-code"
            value={values.mfa_code}
            onChange={(e) => setValues({ ...values, mfa_code: e.target.value.trim() })}
            required
          />
          <Button type="submit" disabled={busy}>
            Send confirmation link
          </Button>
        </form>
      )}
    </Card>
  );
}
