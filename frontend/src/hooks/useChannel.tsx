import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";

/**
 * The app-wide SMS | Email switch.
 *
 * Every channel-aware page reads this one value, so "which channel am I
 * looking at?" is answered consistently instead of each page keeping its own
 * copy. The choice is remembered in localStorage, so a user who works in email
 * stays in email across pages and reloads — but the SMS screens are never more
 * than one click away.
 */

export type Channel = "sms" | "email";

type ChannelContextValue = {
  channel: Channel;
  setChannel: (channel: Channel) => void;
  isEmail: boolean;
  toggle: () => void;
};

const STORAGE_KEY = "sendersms.channel";

function readStored(): Channel {
  if (typeof window === "undefined") return "sms";
  const raw = window.localStorage.getItem(STORAGE_KEY);
  return raw === "email" ? "email" : "sms";
}

const ChannelContext = createContext<ChannelContextValue>({
  channel: "sms",
  setChannel: () => {},
  isEmail: false,
  toggle: () => {},
});

export function ChannelProvider({ children }: { children: React.ReactNode }) {
  const [channel, setChannelState] = useState<Channel>(readStored);

  const setChannel = useCallback((next: Channel) => {
    setChannelState(next);
    try {
      window.localStorage.setItem(STORAGE_KEY, next);
    } catch {
      /* private mode: the in-memory value still works for this session */
    }
  }, []);

  // Keep a second tab in sync.
  useEffect(() => {
    const onStorage = (event: StorageEvent) => {
      if (event.key === STORAGE_KEY) {
        setChannelState(event.newValue === "email" ? "email" : "sms");
      }
    };
    window.addEventListener("storage", onStorage);
    return () => window.removeEventListener("storage", onStorage);
  }, []);

  const value = useMemo<ChannelContextValue>(
    () => ({
      channel,
      setChannel,
      isEmail: channel === "email",
      toggle: () => setChannel(channel === "email" ? "sms" : "email"),
    }),
    [channel, setChannel]
  );

  return <ChannelContext.Provider value={value}>{children}</ChannelContext.Provider>;
}

export function useChannel() {
  return useContext(ChannelContext);
}

export default useChannel;
