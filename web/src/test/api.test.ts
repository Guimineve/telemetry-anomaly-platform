import { describe, expect, it } from "vitest";
import { alertsQuery } from "../api";

describe("alertsQuery", () => {
  it("omits empty filters", () => {
    expect(alertsQuery({})).toBe("");
    expect(alertsQuery({ severity: "", state: "open", limit: 25 })).toBe("?state=open&limit=25");
  });
});
