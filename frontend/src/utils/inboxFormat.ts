/* Formatting helpers shared by the SMS Inbox, Conversations and Opportunity
   pages, so a contact looks the same (same colour, same initials, same time)
   on every screen. */

export const avatarColor = (name: string) => {
  const colors = [
    "bg-primary-600", "bg-primary-700", "bg-primary-800", "bg-[#34B7F1]",
    "bg-warning-400", "bg-accent-500", "bg-primary-400", "bg-warning-400",
  ];
  let h = 0;
  for (let i = 0; i < name.length; i++) h = (h * 31 + name.charCodeAt(i)) % colors.length;
  return colors[h];
};

export const initials = (name: string, phone: string) => {
  if (name && name.trim()) {
    const parts = name.trim().split(/\s+/);
    if (parts.length >= 2) return (parts[0][0] + parts[1][0]).toUpperCase();
    return name.slice(0, 2).toUpperCase();
  }
  return (phone || "??").slice(-2);
};

export const formatListTime = (iso?: string) => {
  if (!iso) return "";
  const d = new Date(iso);
  const now = new Date();
  const diff = (now.getTime() - d.getTime()) / 1000 / 3600;
  if (diff < 24) return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  if (diff < 24 * 7) return d.toLocaleDateString([], { weekday: "short" });
  return d.toLocaleDateString([], { day: "2-digit", month: "short" });
};
