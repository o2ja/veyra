import { useQuery, useQueryClient } from "@tanstack/react-query";
import { type ReactNode, createContext, useContext } from "react";

import type { components } from "../api/schema";
import { api } from "./api/client";
import { ApiError, SESSION_CODES, unwrap } from "./api/errors";

export type Me = components["schemas"]["MeOut"];

interface SessionValue {
  me: Me | null;
  /** The browser presented a session the server no longer accepts (idle, absolute, revoked). */
  expired: boolean;
  loading: boolean;
  can: (permission: string) => boolean;
  refresh: () => Promise<void>;
}

const SessionContext = createContext<SessionValue | null>(null);

export const ME_KEY = ["auth", "me"] as const;

let lastExpired = false;

/** Called by the global API error handler when any request reports `session_expired`. */
export function markSessionExpired(): void {
  lastExpired = true;
}

async function fetchMe(): Promise<Me | null> {
  try {
    const me = await unwrap(api.GET("/api/v1/auth/me"));
    lastExpired = false;
    return me;
  } catch (e) {
    lastExpired = e instanceof ApiError && e.code === "session_expired";
    if (e instanceof ApiError && SESSION_CODES.has(e.code)) return null;
    throw e;
  }
}

export function SessionProvider({ children }: { children: ReactNode }) {
  const client = useQueryClient();
  const query = useQuery({ queryKey: ME_KEY, queryFn: fetchMe, staleTime: 30_000 });
  const me = query.data ?? null;
  const value: SessionValue = {
    me,
    expired: me === null && lastExpired,
    loading: query.isPending,
    // UI hiding only: the server enforces every permission (CLAUDE.md §10).
    can: (permission) => Boolean(me?.permissions.includes(permission)),
    refresh: async () => {
      await client.invalidateQueries();
      await client.refetchQueries({ queryKey: ME_KEY });
    },
  };
  return <SessionContext.Provider value={value}>{children}</SessionContext.Provider>;
}

export function useSession(): SessionValue {
  const value = useContext(SessionContext);
  if (!value) throw new Error("useSession outside SessionProvider");
  return value;
}
