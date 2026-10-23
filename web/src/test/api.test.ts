import { afterEach, describe, expect, it, vi } from "vitest";
import { alertsQuery, fetchAlerts, HttpError, login, setUnauthorizedHandler } from "../api";

describe("alertsQuery", () => {
  it("omits empty filters", () => {
    expect(alertsQuery({})).toBe("");
    expect(alertsQuery({ severity: "", state: "open", limit: 25 })).toBe("?state=open&limit=25");
  });
});

describe("request errors", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    setUnauthorizedHandler(() => {});
  });

  const respond = (status: number, body: unknown) =>
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response(JSON.stringify(body), { status })),
    );

  it("reports 401 on data requests but not on the auth endpoints", async () => {
    const onUnauthorized = vi.fn();
    setUnauthorizedHandler(onUnauthorized);
    respond(401, { detail: "not authenticated" });
    await expect(fetchAlerts()).rejects.toThrow("not authenticated");
    expect(onUnauthorized).toHaveBeenCalledTimes(1);
    await expect(login("a", "b")).rejects.toBeInstanceOf(HttpError);
    expect(onUnauthorized).toHaveBeenCalledTimes(1);
  });

  it("uses the first validation message as the error text", async () => {
    respond(422, { detail: [{ msg: "String should have at least 10 characters" }] });
    await expect(login("a", "b")).rejects.toThrow("at least 10 characters");
  });
});
