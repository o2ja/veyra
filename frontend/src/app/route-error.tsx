import { isRouteErrorResponse, useRouteError } from "react-router-dom";

// Catches render and routing errors. Shows no internals; details stay in the browser console.
export function RouteError() {
  const error = useRouteError();
  const notFound = isRouteErrorResponse(error) && error.status === 404;
  return (
    <div role="alert" className="mx-auto max-w-xl px-4 py-16">
      <h1 className="text-xl font-semibold">{notFound ? "Page not found" : "Something went wrong"}</h1>
      <p className="mt-2 text-muted">
        {notFound
          ? "Check the address and try again."
          : "Reload the page. If it keeps happening, contact support."}
      </p>
      <a href="/" className="mt-6 inline-block text-brand underline">
        Go to start
      </a>
    </div>
  );
}
