import { useState } from "react";

import { ApiError } from "../../lib/api/errors";

/** Submit state for a form that calls the API: busy flag and the typed server error. */
export function useApiSubmit() {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);

  async function submit<T>(action: () => Promise<T>): Promise<T | undefined> {
    setBusy(true);
    setError(null);
    try {
      return await action();
    } catch (e) {
      setError(
        e instanceof ApiError ? e : new ApiError(0, "internal_error", "Something went wrong.", [], null),
      );
      return undefined;
    } finally {
      setBusy(false);
    }
  }

  return { busy, error, submit };
}
