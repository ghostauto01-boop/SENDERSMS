/**
 * In-app change notifications between editors and pickers.
 *
 * Templates and variables are edited on their own pages while template /
 * variable pickers sit inside campaign, creative, inbox and meeting
 * composers. Those pickers used to load once on mount (or cache the variable
 * registry forever), so a rename on the Variables page stayed invisible in
 * every open composer until a full page reload. Pages that mutate data emit
 * an event here; pickers subscribe and refresh themselves in place.
 */

type ChangeChannel = "templates-changed" | "variables-changed";

const listeners = new Map<ChangeChannel, Set<() => void>>();

/** Subscribe to a data-change channel. Returns an unsubscribe function. */
export function onDataChange(channel: ChangeChannel, callback: () => void): () => void {
  let set = listeners.get(channel);
  if (!set) {
    set = new Set();
    listeners.set(channel, set);
  }
  set.add(callback);
  return () => {
    set.delete(callback);
  };
}

/** Tell every subscribed picker/editor that the data changed. */
export function notifyDataChange(channel: ChangeChannel): void {
  const set = listeners.get(channel);
  if (!set) return;
  // Copy so a subscriber that unsubscribes mid-loop cannot break it.
  [...set].forEach((callback) => {
    try {
      callback();
    } catch {
      // One bad listener must not block the rest.
    }
  });
}
