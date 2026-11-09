import { useQuery, useQueryClient } from "@tanstack/react-query";
import { type ReactNode, createContext, useContext } from "react";

import type { components } from "../api/schema";
import { api } from "./api/client";
import { ApiError, SESSION_CODES, unwrap } from "./api/errors";

export type Me = components["schemas"]["MeOut"];

interface SessionValue {
  me: Me | null;
  loading: boolean;
  can: (permission: string) => boolean;
  refresh: () => Promise<void>;
}

const SessionContext = createContext<SessionValue | null>(null);

export const ME_KEY = ["auth", "me"] as const;

async function fetchMe(): Promise<Me | null> {
  try {
    return await unwrap(api.GET("/api/v1/auth/me"));
  } catch (e) {
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
