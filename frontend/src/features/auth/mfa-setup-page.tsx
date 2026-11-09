import { useState } from "react";
import { useNavigate } from "react-router-dom";

import { Alert, AuthCard, Button, ErrorAlert, Field } from "../../components/ui";
import { api } from "../../lib/api/client";
import { unwrap } from "../../lib/api/errors";
import { useSession } from "../../lib/session";
import { useApiSubmit } from "./use-api-form";

/** TOTP enrolment, then the recovery codes, shown exactly once (they are not stored in the browser). */
export function MfaSetupPage() {
  const navigate = useNavigate();
  const { me, refresh } = useSession();
  const { busy, error, submit } = useApiSubmit();
  const [setup, setSetup] = useState<{ otpauth_uri: string; qr_svg_data_uri: string } | null>(null);
  const [code, setCode] = useState("");
  const [codes, setCodes] = useState<string[] | null>(null);
  const [showSecret, setShowSecret] = useState(false);

  async function start() {
    const result = await submit(() => unwrap(api.POST("/api/v1/auth/mfa/setup")));
    if (result) setSetup(result);
  }

  async function enable(event: React.FormEvent) {
    event.preventDefault();
    const result = await submit(() =>
      unwrap(api.POST("/api/v1/auth/mfa/enable", { body: { code: code.trim() } })),
    );
    if (result) setCodes(result.recovery_codes);
  }

  if (codes) {
    return (
      <AuthCard title="Save your recovery codes">
        <Alert tone="info">
          Each code works once if you lose your phone. Store them somewhere safe now: they will not be shown
          again.
        </Alert>
        <ol className="mt-4 grid grid-cols-2 gap-2 font-mono text-sm" aria-label="Recovery codes">
          {codes.map((c) => (
            <li key={c} className="rounded border border-line px-2 py-1">
              {c}
            </li>
          ))}
        </ol>
        <Button
          className="mt-6 w-full"
          onClick={async () => {
            setCodes(null);
            await refresh();
            navigate("/");
          }}
        >
          I saved them, continue
        </Button>
      </AuthCard>
    );
  }

  const secret = setup ? new URL(setup.otpauth_uri).searchParams.get("secret") : null;
  return (
    <AuthCard title="Set up two-factor authentication">
      {me?.enrollment_required && (
        <p className="mb-4 text-sm text-muted">Your workspace requires two-factor authentication.</p>
      )}
      <ErrorAlert error={error} />
      {!setup ? (
        <div className="mt-2 flex flex-col gap-3">
          <p className="text-sm">You need an authenticator app (for example on your phone).</p>
          <Button onClick={start} disabled={busy}>
            Start setup
          </Button>
        </div>
      ) : (
        <form className="mt-2 flex flex-col gap-4" onSubmit={enable}>
          <p className="text-sm">
            Scan this code with your authenticator app, then enter the 6-digit code it shows.
          </p>
          <img
            src={setup.qr_svg_data_uri}
            alt="QR code for your authenticator app"
            className="mx-auto h-48 w-48 bg-white p-2"
          />
          <button
            type="button"
            className="text-left text-sm text-brand underline"
            onClick={() => setShowSecret((s) => !s)}
          >
            {showSecret ? "Hide setup key" : "Can't scan? Show the setup key"}
          </button>
          {showSecret && secret && (
            <code className="break-all rounded border border-line p-2 text-sm">{secret}</code>
          )}
          <Field
            label="6-digit code"
            inputMode="numeric"
            autoComplete="one-time-code"
            value={code}
            onChange={(e) => setCode(e.target.value)}
            required
          />
          <Button type="submit" disabled={busy}>
            Turn on two-factor authentication
          </Button>
        </form>
      )}
    </AuthCard>
  );
}
