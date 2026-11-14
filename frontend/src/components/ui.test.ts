import { cleanup, render } from "@testing-library/react";
import React from "react";
import { afterEach, describe, expect, it } from "vitest";

import { ApiError, toApiError } from "../lib/api/errors";
import { ErrorAlert, RateLimitAlert, rateLimitMessage } from "./ui";

afterEach(() => {
  cleanup();
});

describe("rateLimitMessage helper", () => {
  it("returns null for empty or non-rate-limit errors", () => {
    expect(rateLimitMessage(null)).toBeNull();
    expect(rateLimitMessage(undefined)).toBeNull();
    expect(rateLimitMessage({ status: 200, code: "ok" })).toBeNull();
    expect(rateLimitMessage({ status: 400, code: "validation_error" })).toBeNull();
    expect(rateLimitMessage({ status: 403, code: "forbidden" })).toBeNull();
    expect(rateLimitMessage({ status: 500, code: "internal_error" })).toBeNull();
  });

  it("handles status 429 without Retry-After header/value", () => {
    expect(rateLimitMessage({ status: 429 })).toBe("Too many requests. Try again in a minute.");
    expect(rateLimitMessage({ status: 429, retryAfter: null })).toBe(
      "Too many requests. Try again in a minute.",
    );
    expect(rateLimitMessage({ status: 429, retryAfter: 0 })).toBe(
      "Too many requests. Try again in a minute.",
    );
    expect(rateLimitMessage({ status: 429, retryAfter: -5 })).toBe(
      "Too many requests. Try again in a minute.",
    );
  });

  it("handles status 429 with numeric or string Retry-After", () => {
    expect(rateLimitMessage({ status: 429, retryAfter: 30 })).toBe(
      "Too many requests. Try again in 30 seconds.",
    );
    expect(rateLimitMessage({ status: 429, retryAfter: "45" })).toBe(
      "Too many requests. Try again in 45 seconds.",
    );
    expect(rateLimitMessage({ status: 429, retryAfter: 1 })).toBe(
      "Too many requests. Try again in 1 seconds.",
    );
  });

  it("handles code 'rate_limited'", () => {
    expect(rateLimitMessage({ code: "rate_limited" })).toBe("Too many requests. Try again in a minute.");
    expect(rateLimitMessage({ code: "rate_limited", retryAfter: 15 })).toBe(
      "Too many requests. Try again in 15 seconds.",
    );
  });

  it("handles code 'too_many_extractions'", () => {
    expect(rateLimitMessage({ code: "too_many_extractions" })).toBe(
      "Too many requests. Try again in a minute.",
    );
    expect(rateLimitMessage({ code: "too_many_extractions", retryAfter: 60 })).toBe(
      "Too many requests. Try again in 60 seconds.",
    );
  });
});

describe("toApiError integration with Retry-After", () => {
  it("extracts Retry-After from response headers into ApiError", () => {
    const response = new Response(null, {
      status: 429,
      headers: { "Retry-After": "25" },
    });
    const error = toApiError(response, {
      error: { code: "rate_limited", message: "Too many requests. Try again later." },
    });
    expect(error.status).toBe(429);
    expect(error.code).toBe("rate_limited");
    expect(error.retryAfter).toBe(25);
    expect(rateLimitMessage(error)).toBe("Too many requests. Try again in 25 seconds.");
  });

  it("falls back to a minute when Retry-After is absent", () => {
    const response = new Response(null, { status: 429 });
    const error = toApiError(response, {
      error: { code: "rate_limited", message: "Too many requests. Try again later." },
    });
    expect(error.status).toBe(429);
    expect(error.retryAfter).toBeNull();
    expect(rateLimitMessage(error)).toBe("Too many requests. Try again in a minute.");
  });

  it("extracts retry_after from envelope body if header is omitted", () => {
    const response = new Response(null, { status: 429 });
    const error = toApiError(response, {
      error: { code: "rate_limited", retry_after: 50 },
    });
    expect(error.retryAfter).toBe(50);
    expect(rateLimitMessage(error)).toBe("Too many requests. Try again in 50 seconds.");
  });
});

describe("RateLimitAlert and ErrorAlert components", () => {
  it("renders RateLimitAlert with role='alert' and friendly message", () => {
    const error = new ApiError(429, "rate_limited", "Raw error", [], null, 30);
    const { getByRole } = render(React.createElement(RateLimitAlert, { error }));
    const alert = getByRole("alert");
    expect(alert).toBeDefined();
    expect(alert.textContent).toBe("Too many requests. Try again in 30 seconds.");
  });

  it("renders RateLimitAlert as null for non-rate-limit errors", () => {
    const error = new ApiError(400, "bad_request", "Bad request", [], null);
    const { container } = render(React.createElement(RateLimitAlert, { error }));
    expect(container.firstChild).toBeNull();
  });

  it("renders ErrorAlert with friendly rate limit message and role='alert' when rate-limited", () => {
    const error = new ApiError(429, "rate_limited", "Raw error", [], "req_123", null);
    const { getByRole } = render(React.createElement(ErrorAlert, { error }));
    const alert = getByRole("alert");
    expect(alert).toBeDefined();
    expect(alert.textContent).toContain("Too many requests. Try again in a minute.");
    expect(alert.textContent).toContain("Reference: req_123");
  });

  it("renders ErrorAlert with normal message for standard errors", () => {
    const error = new ApiError(404, "not_found", "Item missing", [], null);
    const { getByRole } = render(React.createElement(ErrorAlert, { error }));
    const alert = getByRole("alert");
    expect(alert).toBeDefined();
    expect(alert.textContent).toBe("Item missing");
  });

  it("renders ErrorAlert as null when error is null or undefined", () => {
    const { container } = render(React.createElement(ErrorAlert, { error: null }));
    expect(container.firstChild).toBeNull();
  });
});
