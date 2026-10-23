import { act, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AlertFeed } from "../components/AlertFeed";
import type { LiveAlert } from "../types";
import { mergeAlert, useAlertStream, wsUrl } from "../useAlertStream";

const alert = (id: string, over: Partial<LiveAlert> = {}): LiveAlert => ({
  id,
  ruleId: "r1",
  ruleName: "Temperature too high",
  ruleType: "threshold",
  deviceId: "sensor-001",
  metric: "temperature",
  severity: "critical",
  state: "open",
  message: "temperature 95 above max 60",
  value: 95,
  timestamp: "2026-01-01T10:00:00.000Z",
  ...over,
});

describe("mergeAlert", () => {
  it("puts the newest first and de-duplicates by id", () => {
    const list = mergeAlert(mergeAlert([], alert("a")), alert("b"));
    expect(list.map((a) => a.id)).toEqual(["b", "a"]);
    const updated = mergeAlert(list, alert("a", { state: "resolved" }));
    expect(updated.map((a) => a.id)).toEqual(["a", "b"]);
    expect(updated[0].state).toBe("resolved");
  });

  it("caps the list", () => {
    let list: LiveAlert[] = [];
    for (let i = 0; i < 10; i++) list = mergeAlert(list, alert(String(i)), 3);
    expect(list.map((a) => a.id)).toEqual(["9", "8", "7"]);
  });
});

describe("wsUrl", () => {
  it("follows the page protocol", () => {
    expect(wsUrl({ protocol: "http:", host: "localhost:5173" })).toBe("ws://localhost:5173/ws/alerts");
    expect(wsUrl({ protocol: "https:", host: "x.example" })).toBe("wss://x.example/ws/alerts");
  });
});

class FakeSocket {
  static instances: FakeSocket[] = [];
  onopen: (() => void) | null = null;
  onmessage: ((ev: { data: string }) => void) | null = null;
  onclose: (() => void) | null = null;
  closed = false;
  constructor(public url: string) {
    FakeSocket.instances.push(this);
  }
  close() {
    this.closed = true;
  }
}

function Probe() {
  const { alerts, status } = useAlertStream("ws://test/ws/alerts");
  return <AlertFeed alerts={alerts} status={status} />;
}

describe("useAlertStream", () => {
  beforeEach(() => {
    FakeSocket.instances = [];
    vi.useFakeTimers();
    vi.stubGlobal("WebSocket", FakeSocket);
  });
  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  it("shows alerts pushed over the socket", () => {
    render(<Probe />);
    expect(screen.getByText("Connecting…")).toBeInTheDocument();
    const sock = FakeSocket.instances[0];
    act(() => sock.onopen?.());
    expect(screen.getByText("Live")).toBeInTheDocument();
    act(() => sock.onmessage?.({ data: JSON.stringify(alert("a1")) }));
    expect(screen.getByText("temperature 95 above max 60")).toBeInTheDocument();
    expect(screen.getByText("sensor-001")).toBeInTheDocument();
  });

  it("ignores pong and malformed frames", () => {
    render(<Probe />);
    const sock = FakeSocket.instances[0];
    act(() => sock.onopen?.());
    act(() => sock.onmessage?.({ data: "pong" }));
    act(() => sock.onmessage?.({ data: "{not json" }));
    expect(screen.getByText("Waiting for alerts…")).toBeInTheDocument();
  });

  it("reconnects with backoff after the connection drops", () => {
    render(<Probe />);
    act(() => FakeSocket.instances[0].onclose?.());
    expect(screen.getByText("Reconnecting…")).toBeInTheDocument();
    expect(FakeSocket.instances).toHaveLength(1);
    act(() => vi.advanceTimersByTime(1000));
    expect(FakeSocket.instances).toHaveLength(2);
    act(() => FakeSocket.instances[1].onclose?.());
    act(() => vi.advanceTimersByTime(1000));
    expect(FakeSocket.instances).toHaveLength(2); // second delay is 2s
    act(() => vi.advanceTimersByTime(1000));
    expect(FakeSocket.instances).toHaveLength(3);
  });

  it("closes the socket and stops reconnecting on unmount", () => {
    const { unmount } = render(<Probe />);
    const sock = FakeSocket.instances[0];
    unmount();
    expect(sock.closed).toBe(true);
    act(() => sock.onclose?.());
    act(() => vi.advanceTimersByTime(60000));
    expect(FakeSocket.instances).toHaveLength(1);
  });
});
