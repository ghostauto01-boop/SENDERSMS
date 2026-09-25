import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import api from "./client";
import { getDbOutage, reportDbOk } from "../utils/dbStatus";

/**
 * The response interceptor is the bridge between the API's
 * `503 {error_kind: "database"}` answers and the global outage banner.
 */

function runInterceptors(kind: "fulfilled" | "rejected", value: any) {
  // axios keeps handlers on `interceptors.response.handlers`.
  const handlers = (api.interceptors.response as any).handlers as Array<{
    fulfilled: (v: any) => any;
    rejected: (e: any) => any;
  }>;
  const h = handlers[0];
  return kind === "fulfilled" ? h.fulfilled(value) : h.rejected(value);
}

beforeEach(() => {
  reportDbOk();
});

afterEach(() => {
  reportDbOk();
});

describe("api client interceptor", () => {
  it("records a database outage from a 503 with error_kind=database", async () => {
    const err = {
      response: {
        status: 503,
        data: {
          detail: "Database unavailable: Your account or project has exceeded the compute time quota.",
          error_kind: "database",
          db: { kind: "quota_exceeded", message: "exceeded the compute time quota", hint: "Neon" },
        },
      },
    };
    await expect(runInterceptors("rejected", err)).rejects.toBe(err);
    expect(getDbOutage()?.kind).toBe("quota_exceeded");
  });

  it("clears the outage on the next successful data response", async () => {
    await runInterceptors("rejected", {
      response: { status: 503, data: { error_kind: "database", db: { kind: "unreachable", message: "x" } } },
    }).catch(() => {});
    expect(getDbOutage()).not.toBeNull();
    runInterceptors("fulfilled", { config: { url: "/contacts/" }, data: {} });
    expect(getDbOutage()).toBeNull();
  });

  it("does not treat the DB-less /health probe as proof of recovery", async () => {
    await runInterceptors("rejected", {
      response: { status: 503, data: { error_kind: "database", db: { kind: "unreachable", message: "x" } } },
    }).catch(() => {});
    runInterceptors("fulfilled", { config: { url: "/health" }, data: { status: "ok" } });
    expect(getDbOutage()).not.toBeNull();
  });

  it("does not treat a cookie-less 401 as proof of recovery", async () => {
    await runInterceptors("rejected", {
      response: { status: 503, data: { error_kind: "database", db: { kind: "unreachable", message: "x" } } },
    }).catch(() => {});
    const href = window.location.href;
    await runInterceptors("rejected", { response: { status: 401, data: { detail: "Not authenticated" } } }).catch(
      () => {}
    );
    expect(getDbOutage()).not.toBeNull();
    // (jsdom cannot navigate; just make sure we did not throw.)
    expect(typeof href).toBe("string");
  });

  it("ignores plain 500s and 4xx errors", async () => {
    await runInterceptors("rejected", { response: { status: 500, data: { detail: "boom" } } }).catch(() => {});
    expect(getDbOutage()).toBeNull();
    await runInterceptors("rejected", { response: { status: 404, data: { detail: "nope" } } }).catch(() => {});
    expect(getDbOutage()).toBeNull();
  });

  it("still flattens 422 validation arrays into a readable string", async () => {
    const err = {
      response: {
        status: 422,
        data: { detail: [{ loc: ["body", "phone_number"], msg: "field required" }] },
      },
    };
    await runInterceptors("rejected", err).catch(() => {});
    expect(err.response.data.detail).toBe("phone_number: field required");
  });
});

describe("visibility-aware polling", () => {
  it("skips ticks while the tab is hidden and refreshes when it returns", async () => {
    vi.useFakeTimers();
    const { renderHook } = await import("@testing-library/react");
    const { useVisiblePolling } = await import("../hooks/useVisiblePolling");
    const cb = vi.fn();
    let hidden = false;
    Object.defineProperty(document, "hidden", { configurable: true, get: () => hidden });

    renderHook(() => useVisiblePolling(cb, 1000));
    vi.advanceTimersByTime(2000);
    expect(cb).toHaveBeenCalledTimes(2);

    hidden = true;
    vi.advanceTimersByTime(5000);
    expect(cb).toHaveBeenCalledTimes(2);

    hidden = false;
    document.dispatchEvent(new Event("visibilitychange"));
    expect(cb).toHaveBeenCalledTimes(3);

    vi.useRealTimers();
    Object.defineProperty(document, "hidden", { configurable: true, get: () => false });
  });
});
