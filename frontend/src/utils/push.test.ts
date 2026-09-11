import { describe, it, expect, vi, beforeEach } from "vitest";
import { disablePush, enablePush, pushState, pushSupported } from "./push";

const apiGet = vi.fn();
const apiPost = vi.fn();

vi.mock("../api/client", () => ({
  default: {
    get: (...args: any[]) => apiGet(...args),
    post: (...args: any[]) => apiPost(...args),
  },
}));

beforeEach(() => {
  apiGet.mockReset();
  apiPost.mockReset();
  vi.unstubAllGlobals();
});

describe("pushSupported", () => {
  it("is false without serviceWorker/PushManager/Notification", () => {
    vi.stubGlobal("navigator", {});
    expect(pushSupported()).toBe(false);
  });
});

describe("pushState", () => {
  it("returns unsupported when APIs are missing", async () => {
    vi.stubGlobal("navigator", {});
    expect(await pushState()).toBe("unsupported");
  });

  it("returns blocked when permission was denied", async () => {
    vi.stubGlobal("navigator", { serviceWorker: {} });
    vi.stubGlobal("PushManager", class {});
    vi.stubGlobal("Notification", { permission: "denied" });
    expect(await pushState()).toBe("blocked");
  });
});

describe("enablePush/disablePush", () => {
  it("enablePush returns unsupported without APIs", async () => {
    vi.stubGlobal("navigator", {});
    expect(await enablePush()).toBe("unsupported");
  });

  it("disablePush without a subscription resolves to off", async () => {
    const pushManager = { getSubscription: vi.fn().mockResolvedValue(null) };
    const ready = Promise.resolve({ pushManager });
    vi.stubGlobal("navigator", { serviceWorker: { ready } });
    vi.stubGlobal("PushManager", class {});
    vi.stubGlobal("Notification", { permission: "granted" });
    expect(await disablePush()).toBe("off");
    expect(apiPost).not.toHaveBeenCalled();
  });

  it("enablePush subscribes and posts keys to the server", async () => {
    const json = vi.fn().mockReturnValue({
      endpoint: "https://push.example.com/sub1",
      keys: { p256dh: "PUB", auth: "AUTH" },
    });
    const subscribe = vi.fn().mockResolvedValue({ toJSON: json });
    const pushManager = {
      getSubscription: vi.fn().mockResolvedValue(null),
      subscribe,
    };
    const ready = Promise.resolve({ pushManager });
    vi.stubGlobal("navigator", {
      serviceWorker: { ready },
      userAgent: "vitest",
    });
    vi.stubGlobal("PushManager", class {});
    vi.stubGlobal("Notification", {
      permission: "default",
      requestPermission: vi.fn().mockResolvedValue("granted"),
    });
    apiGet.mockResolvedValue({ data: { public_key: "Q".repeat(87) } });
    apiPost.mockResolvedValue({ data: { success: true, devices: 1 } });

    expect(await enablePush()).toBe("on");
    expect(apiGet).toHaveBeenCalledWith("/notifications/push/vapid-key");
    expect(subscribe).toHaveBeenCalledTimes(1);
    expect(apiPost).toHaveBeenCalledWith("/notifications/push/subscribe", {
      endpoint: "https://push.example.com/sub1",
      keys: { p256dh: "PUB", auth: "AUTH" },
      user_agent: "vitest",
    });
  });
});
