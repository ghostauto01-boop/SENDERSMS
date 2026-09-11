/** Inbuilt browser notifications (VAPID Web Push — free, no third party). */
import api from "../api/client";

export type PushState = "unsupported" | "blocked" | "off" | "on" | "unknown";

function b64ToBytes(b64: string): BufferSource {
  const pad = "=".repeat((4 - (b64.length % 4)) % 4);
  const raw = atob((b64 + pad).replace(/-/g, "+").replace(/_/g, "/"));
  const out = new Uint8Array(raw.length);
  for (let i = 0; i < raw.length; i++) out[i] = raw.charCodeAt(i);
  return out as BufferSource;
}

export function pushSupported(): boolean {
  return (
    typeof window !== "undefined" &&
    "serviceWorker" in navigator &&
    "PushManager" in window &&
    "Notification" in window
  );
}

export async function swRegistration(): Promise<ServiceWorkerRegistration | null> {
  if (!pushSupported()) return null;
  try {
    return (await navigator.serviceWorker.ready) || null;
  } catch {
    return null;
  }
}

/** Current state of this browser: on / off / blocked / unsupported. */
export async function pushState(): Promise<PushState> {
  if (!pushSupported()) return "unsupported";
  if (Notification.permission === "denied") return "blocked";
  try {
    const reg = await swRegistration();
    const sub = await reg?.pushManager.getSubscription();
    return sub ? "on" : "off";
  } catch {
    return "unknown";
  }
}

/**
 * Ask the browser for notification permission (this is the prompt the user
 * sees on their phone) and subscribe this device. Returns the new state.
 */
export async function enablePush(): Promise<PushState> {
  if (!pushSupported()) return "unsupported";
  const perm = await Notification.requestPermission();
  if (perm !== "granted") return "blocked";
  const reg = await swRegistration();
  if (!reg) return "unknown";
  let sub = await reg.pushManager.getSubscription();
  if (!sub) {
    const { data } = await api.get("/notifications/push/vapid-key");
    sub = await reg.pushManager.subscribe({
      userVisibleOnly: true,
      applicationServerKey: b64ToBytes(data.public_key),
    });
  }
  const json = sub.toJSON();
  await api.post("/notifications/push/subscribe", {
    endpoint: json.endpoint,
    keys: { p256dh: json.keys?.p256dh, auth: json.keys?.auth },
    user_agent: navigator.userAgent,
  });
  return "on";
}

/** Unsubscribe this browser and remove it from the server. */
export async function disablePush(): Promise<PushState> {
  try {
    const reg = await swRegistration();
    const sub = await reg?.pushManager.getSubscription();
    if (sub) {
      const json = sub.toJSON();
      await sub.unsubscribe().catch(() => {});
      await api
        .post("/notifications/push/unsubscribe", {
          endpoint: json.endpoint,
          keys: { p256dh: "x", auth: "x" },
        })
        .catch(() => {});
    }
  } catch {
    /* already off */
  }
  return "off";
}

export interface PushDevice {
  id: number;
  endpoint_host: string;
  user_agent: string;
  created_at: string | null;
  last_used_at: string | null;
}

export async function listDevices(): Promise<PushDevice[]> {
  const { data } = await api.get("/notifications/push/subscriptions");
  return data.items ?? [];
}

export async function sendTestPush(): Promise<{
  devices: number;
  pushed: boolean;
  note: string;
}> {
  const { data } = await api.post("/notifications/push/test");
  return data;
}
