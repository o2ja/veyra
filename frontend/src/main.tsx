import "./index.css";

import { MutationCache, QueryCache, QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { RouterProvider, createBrowserRouter } from "react-router-dom";

import { routes } from "./app/routes";
import { StepUpProvider } from "./components/step-up";
import { ApiError, SESSION_CODES } from "./lib/api/errors";
import { ME_KEY, SessionProvider } from "./lib/session";

const root = document.getElementById("root");
if (!root) throw new Error("#root element missing");

const router = createBrowserRouter(routes);

// Session-expiry handling: any API call that finds the session gone sends the user to sign in,
// keeping where they were. The server decides; the UI only follows.
function onApiError(error: unknown) {
  if (!(error instanceof ApiError)) return;
  const here = window.location.pathname;
  if (SESSION_CODES.has(error.code) && !here.startsWith("/login")) {
    queryClient.setQueryData(ME_KEY, null);
    void router.navigate(`/login?reason=expired&next=${encodeURIComponent(here)}`);
  } else if (error.code === "mfa_enrollment_required") {
    void router.navigate("/mfa/setup");
  } else if (error.code === "mfa_required") {
    void router.navigate(`/login/mfa?next=${encodeURIComponent(here)}`);
  }
}

const queryClient = new QueryClient({
  queryCache: new QueryCache({ onError: onApiError }),
  mutationCache: new MutationCache({ onError: onApiError }),
  defaultOptions: {
    queries: { retry: (count, error) => !(error instanceof ApiError && error.status < 500) && count < 2 },
  },
});

createRoot(root).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <SessionProvider>
        <StepUpProvider>
          <RouterProvider router={router} />
        </StepUpProvider>
      </SessionProvider>
    </QueryClientProvider>
  </StrictMode>,
);
