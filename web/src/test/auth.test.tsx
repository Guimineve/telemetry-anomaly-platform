import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { App } from "../App";
import { AuthProvider } from "../auth";
import type { User } from "../types";

const viewer: User = {
  id: "u1",
  username: "vic",
  role: "viewer",
  isActive: true,
  createdAt: null,
  lastLoginAt: null,
};
const admin: User = { ...viewer, id: "u0", username: "root", role: "admin" };

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });

type Handler = (method: string, body: unknown) => Response;
let routes: Record<string, Handler>;
let calls: { method: string; url: string; body: unknown }[];

const rule = {
  id: "r1",
  name: "Temperature too high",
  type: "threshold",
  metric: "temperature",
  devicePattern: "*",
  params: { max: 60 },
  severity: "critical",
  enabled: true,
};

function backend(me: User | null) {
  let session = me;
  routes = {
    "/api/v1/auth/me": () => (session ? json(session) : json({ detail: "not authenticated" }, 401)),
    "/api/v1/auth/login": (_m, body) => {
      const { username, password } = body as { username: string; password: string };
      if (password !== "right-password") return json({ detail: "invalid username or password" }, 401);
      session = username === "root" ? admin : { ...viewer, username };
      return json(session);
    },
    "/api/v1/auth/register": (_m, body) => {
      session = { ...viewer, username: (body as { username: string }).username };
      return json(session, 201);
    },
    "/api/v1/auth/logout": () => {
      session = null;
      return new Response(null, { status: 204 });
    },
    "/api/v1/alerts": () =>
      session ? json({ items: [], total: 0, limit: 25, offset: 0 }) : json({ detail: "x" }, 401),
    "/api/v1/rules": () => json([rule]),
    "/api/v1/users": () => json([admin, viewer]),
    "/api/v1/auth/password": (_m, body) =>
      (body as { currentPassword: string }).currentPassword === "right-password"
        ? new Response(null, { status: 204 })
        : json({ detail: "current password is wrong" }, 400),
    "/api/v1/auth/logout-all": () => {
      session = null;
      return new Response(null, { status: 204 });
    },
    "/api/v1/users/u1/password": () => new Response(null, { status: 204 }),
  };
}

class FakeSocket {
  onopen = null;
  onmessage = null;
  onclose = null;
  close() {}
}

beforeEach(() => {
  calls = [];
  vi.stubGlobal("WebSocket", FakeSocket);
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init: RequestInit = {}) => {
      const method = init.method ?? "GET";
      const body = init.body ? JSON.parse(init.body as string) : undefined;
      calls.push({ method, url, body });
      const handler = routes[url.split("?")[0]];
      return handler ? handler(method, body) : json({ detail: "not found" }, 404);
    }),
  );
});
afterEach(() => vi.unstubAllGlobals());

const renderApp = () =>
  render(
    <AuthProvider>
      <App />
    </AuthProvider>,
  );

function fill(username: string, password: string) {
  fireEvent.change(screen.getByLabelText("Username"), { target: { value: username } });
  fireEvent.change(screen.getByLabelText("Password"), { target: { value: password } });
}

describe("login flow", () => {
  it("shows the login page without a session, then the dashboard after logging in", async () => {
    backend(null);
    renderApp();
    expect(await screen.findByRole("heading", { name: "Log in" })).toBeInTheDocument();
    fill("vic", "right-password");
    fireEvent.click(screen.getByRole("button", { name: "Log in" }));
    expect(await screen.findByText("vic · viewer")).toBeInTheDocument();
    expect(await screen.findByText("Temperature too high")).toBeInTheDocument();
    // viewers see rules but cannot change them, and do not see user management
    expect(screen.queryByRole("button", { name: "Delete" })).not.toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Users" })).not.toBeInTheDocument();
  });

  it("shows the server's error on a failed login", async () => {
    backend(null);
    renderApp();
    await screen.findByRole("heading", { name: "Log in" });
    fill("vic", "wrong");
    fireEvent.click(screen.getByRole("button", { name: "Log in" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("invalid username or password");
  });

  it("registers a new account", async () => {
    backend(null);
    renderApp();
    fireEvent.click(await screen.findByRole("button", { name: "No account? Create one" }));
    fill("newbie", "long-enough-password");
    fireEvent.click(screen.getByRole("button", { name: "Create account" }));
    expect(await screen.findByText("newbie · viewer")).toBeInTheDocument();
    expect(calls.find((c) => c.url === "/api/v1/auth/register")?.body).toEqual({
      username: "newbie",
      password: "long-enough-password",
    });
  });

  it("restores an existing session and lets an admin manage users and rules", async () => {
    backend(admin);
    renderApp();
    expect(await screen.findByText("root · admin")).toBeInTheDocument();
    expect(await screen.findByRole("heading", { name: "Users" })).toBeInTheDocument();
    expect(await screen.findByRole("button", { name: "Delete" })).toBeInTheDocument();
    expect(await screen.findByLabelText("Role of root")).toBeDisabled(); // cannot demote yourself
    expect(screen.getByLabelText("Role of vic")).toBeEnabled();
  });

  it("logs out", async () => {
    backend(viewer);
    renderApp();
    fireEvent.click(await screen.findByRole("button", { name: "Log out" }));
    expect(await screen.findByRole("heading", { name: "Log in" })).toBeInTheDocument();
  });

  it("returns to the login page when a request gets 401", async () => {
    backend(viewer);
    renderApp();
    await screen.findByText("vic · viewer");
    // the session ends on the server (expired or user disabled); the next request is refused
    await act(async () => {
      await routes["/api/v1/auth/logout"]("POST", undefined).text();
      fireEvent.change(screen.getByLabelText("State"), { target: { value: "open" } });
    });
    await waitFor(() =>
      expect(screen.getByRole("heading", { name: "Log in" })).toBeInTheDocument(),
    );
  });
});

describe("account lifecycle", () => {
  it("changes the password and reports a wrong current password", async () => {
    backend(viewer);
    renderApp();
    await screen.findByText("vic · viewer");
    const current = screen.getByLabelText("Current password");
    const next = screen.getByLabelText("New password");
    fireEvent.change(current, { target: { value: "wrong" } });
    fireEvent.change(next, { target: { value: "a-new-password" } });
    fireEvent.click(screen.getByRole("button", { name: "Change password" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("current password is wrong");

    fireEvent.change(current, { target: { value: "right-password" } });
    fireEvent.click(screen.getByRole("button", { name: "Change password" }));
    expect(await screen.findByText(/other sessions were logged out/)).toBeInTheDocument();
    expect(calls.filter((c) => c.url === "/api/v1/auth/password").at(-1)?.body).toEqual({
      currentPassword: "right-password",
      newPassword: "a-new-password",
    });
  });

  it("logs out everywhere and returns to the login page", async () => {
    backend(viewer);
    renderApp();
    fireEvent.click(await screen.findByRole("button", { name: "Log out everywhere" }));
    expect(await screen.findByRole("heading", { name: "Log in" })).toBeInTheDocument();
    expect(calls.some((c) => c.url === "/api/v1/auth/logout-all" && c.method === "POST")).toBe(true);
  });

  it("lets an admin reset another user's password", async () => {
    backend(admin);
    renderApp();
    await screen.findByText("root · admin");
    fireEvent.click(await screen.findByRole("button", { name: "Reset password" }));
    fireEvent.change(screen.getByLabelText("New password for vic"), {
      target: { value: "reset-password-123" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(await screen.findByText(/Password for vic was reset/)).toBeInTheDocument();
    expect(calls.find((c) => c.url === "/api/v1/users/u1/password")?.body).toEqual({
      newPassword: "reset-password-123",
    });
  });
});
