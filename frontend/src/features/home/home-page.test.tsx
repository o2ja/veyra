import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";

import { HomePage } from "./home-page";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

function stubFetch(response: Response | Error) {
  const fetchMock = vi.fn((input: Request) => {
    expect(new URL(input.url).pathname).toBe("/api/v1/health/live");
    return response instanceof Error ? Promise.reject(response) : Promise.resolve(response);
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

test("shows live when the health endpoint answers ok", async () => {
  const fetchMock = stubFetch(
    new Response(JSON.stringify({ status: "ok" }), { headers: { "Content-Type": "application/json" } }),
  );
  render(<HomePage />);
  expect(await screen.findByText("live")).toBeTruthy();
  expect(fetchMock).toHaveBeenCalledOnce();
});

test("shows unreachable on a network failure", async () => {
  stubFetch(new TypeError("network down"));
  render(<HomePage />);
  expect(await screen.findByText("unreachable")).toBeTruthy();
});

test("shows unreachable on an error status", async () => {
  stubFetch(new Response("{}", { status: 503, headers: { "Content-Type": "application/json" } }));
  render(<HomePage />);
  expect(await screen.findByText("unreachable")).toBeTruthy();
});
