import * as Dialog from "@radix-ui/react-dialog";
import { type ReactNode, createContext, useCallback, useContext, useRef, useState } from "react";

import { api } from "../lib/api/client";
import { ApiError, unwrap } from "../lib/api/errors";
import { useSession } from "../lib/session";
import { Button, ErrorAlert, Field } from "./ui";

// Step-up (SECURITY.md §1): when the server answers `reauthentication_required`, ask for the password
// (and an authenticator code), then retry the original action once.

type Run = <T>(action: () => Promise<T>) => Promise<T>;

const StepUpContext = createContext<Run | null>(null);

export function StepUpProvider({ children }: { children: ReactNode }) {
  const { me } = useSession();
  const [open, setOpen] = useState(false);
  const [password, setPassword] = useState("");
  const [code, setCode] = useState("");
  const [error, setError] = useState<ApiError | null>(null);
  const [busy, setBusy] = useState(false);
  const pending = useRef<{ resolve: () => void; reject: (e: unknown) => void } | null>(null);

  const ask = useCallback(
    () =>
      new Promise<void>((resolve, reject) => {
        pending.current = { resolve, reject };
        setPassword("");
        setCode("");
        setError(null);
        setOpen(true);
      }),
    [],
  );

  const run: Run = useCallback(
    async (action) => {
      try {
        return await action();
      } catch (e) {
        if (!(e instanceof ApiError) || e.code !== "reauthentication_required") throw e;
        await ask();
        return action();
      }
    },
    [ask],
  );

  async function confirm(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    try {
      await unwrap(
        api.POST("/api/v1/auth/reauthenticate", {
          body: { password, mfa_code: me?.user.mfa_enabled ? code : null },
        }),
      );
      setOpen(false);
      pending.current?.resolve();
    } catch (e) {
      setError(e instanceof ApiError ? e : null);
    } finally {
      setBusy(false);
    }
  }

  function cancel() {
    setOpen(false);
    pending.current?.reject(new ApiError(0, "cancelled", "Cancelled.", [], null));
  }

  return (
    <StepUpContext.Provider value={run}>
      {children}
      <Dialog.Root open={open} onOpenChange={(next) => !next && cancel()}>
        <Dialog.Portal>
          <Dialog.Overlay className="fixed inset-0 bg-black/40" />
          <Dialog.Content className="fixed left-1/2 top-1/3 w-[min(28rem,92vw)] -translate-x-1/2 rounded-lg border border-line bg-panel p-6 shadow-lg">
            <Dialog.Title className="text-lg font-semibold">Confirm it's you</Dialog.Title>
            <Dialog.Description className="mt-1 text-sm text-muted">
              This action needs your password{me?.user.mfa_enabled ? " and an authenticator code" : ""}.
            </Dialog.Description>
            <form className="mt-4 flex flex-col gap-3" onSubmit={confirm}>
              <ErrorAlert error={error} />
              <Field
                label="Password"
                type="password"
                autoComplete="current-password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                required
              />
              {me?.user.mfa_enabled && (
                <Field
                  label="Authenticator code"
                  inputMode="numeric"
                  autoComplete="one-time-code"
                  value={code}
                  onChange={(e) => setCode(e.target.value.trim())}
                  required
                />
              )}
              <div className="flex justify-end gap-2">
                <Button variant="secondary" onClick={cancel}>
                  Cancel
                </Button>
                <Button type="submit" disabled={busy}>
                  Confirm
                </Button>
              </div>
            </form>
          </Dialog.Content>
        </Dialog.Portal>
      </Dialog.Root>
    </StepUpContext.Provider>
  );
}

export function useStepUp(): Run {
  const run = useContext(StepUpContext);
  if (!run) throw new Error("useStepUp outside StepUpProvider");
  return run;
}
