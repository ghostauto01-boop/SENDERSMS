import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useLocation, Outlet, useNavigate } from "react-router-dom";
import { useAuth } from "../hooks/useAuth";
import {
  LayoutDashboard, Users, List, Megaphone, GitBranch, Sparkles,
  Inbox, Send, Clock, FileText, BarChart3, Settings, MessageSquareReply,
  Zap, Moon, Sun, Menu, X, LogOut, Search, Braces, Repeat, Calendar, Target,
  Activity, Phone, Mail, MailOpen, GraduationCap, MoreHorizontal,
} from "lucide-react";
import BrandMark from "../components/BrandMark";
import ChannelSwitch from "../components/ChannelSwitch";
import NotificationBell from "../components/NotificationBell";
import guideApi from "../api/guide";

/**
 * The app shell.
 *
 * MOBILE FIRST, because this is a phone app that also happens to run on a
 * desktop: the sidebar is a drawer on small screens, and the five destinations
 * you actually use all day (Overview, Send, SMS inbox, Email inbox, Contacts)
 * sit in a thumb-reachable bottom bar with safe-area padding for notched
 * phones. Everything else lives behind "More", which opens the same drawer, so
 * no page ever became less reachable by adding the bar.
 *
 * Page changes animate once, briefly (see `.page-enter`), keyed on the route so
 * a re-render never replays it.
 */

interface NavItem {
  to: string;
  label: string;
  icon: typeof Activity;
}

const OVERVIEW: NavItem[] = [
  { to: "/overview", label: "Overview", icon: Activity },
  { to: "/dashboard", label: "Dashboard", icon: LayoutDashboard },
  { to: "/setup", label: "Setup Guide", icon: GraduationCap },
];

const WORK: NavItem[] = [
  { to: "/send", label: "Send", icon: Send },
  { to: "/contacts", label: "Contacts", icon: Users },
  { to: "/phone", label: "Phone", icon: Phone },
  { to: "/lists", label: "Lists", icon: List },
  { to: "/audiences", label: "Audiences", icon: Target },
  { to: "/campaigns", label: "Campaigns", icon: Megaphone },
];

const CHANNELS: NavItem[] = [
  { to: "/sms-manager", label: "SMS Manager", icon: Sparkles },
  { to: "/email-manager", label: "Email Manager", icon: Mail },
  { to: "/email-inbox", label: "Email Inbox", icon: MailOpen },
  { to: "/inbox", label: "Inbox", icon: Inbox },
  { to: "/calendar", label: "Calendar", icon: Calendar },
];

const AUTOMATION: NavItem[] = [
  { to: "/sequences", label: "Sequences", icon: GitBranch },
  { to: "/auto-reply", label: "Auto-Reply", icon: MessageSquareReply },
  { to: "/automations", label: "Automations", icon: Zap },
  { to: "/campaign-follow-ups", label: "Follow-up", icon: Repeat },
  { to: "/follow-ups", label: "Follow-ups", icon: Clock },
];

const CONTENT: NavItem[] = [
  { to: "/variables", label: "Variables", icon: Braces },
  { to: "/templates", label: "Templates", icon: FileText },
  { to: "/analytics", label: "Analytics", icon: BarChart3 },
  { to: "/settings", label: "Settings", icon: Settings },
];

//: Thumb-bar destinations. Five is the most that stays comfortable on a phone.
const TAB_BAR: NavItem[] = [
  { to: "/overview", label: "Overview", icon: Activity },
  { to: "/send", label: "Send", icon: Send },
  { to: "/inbox", label: "SMS", icon: Inbox },
  { to: "/email-inbox", label: "Email", icon: MailOpen },
  { to: "/contacts", label: "Contacts", icon: Users },
];

export default function MainLayout() {
  const [sidebarOpen, setSidebarOpen] = useState(false);
  /** Steps in the setup guide that are not done yet; null until the first answer. */
  const [setupBlocking, setSetupBlocking] = useState<number | null>(null);
  const [search, setSearch] = useState("");
  const searchRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    const load = () =>
      guideApi
        .summary()
        .then((s) => setSetupBlocking(s.blocking))
        .catch(() => setSetupBlocking(null));
    load();
    // The guide page announces its own re-checks, so the badge updates the moment
    // a step is fixed instead of on the next full reload.
    window.addEventListener("setup-guide-updated", load);
    return () => window.removeEventListener("setup-guide-updated", load);
  }, []);

  const [dark, setDark] = useState(() => {
    if (typeof window !== "undefined") {
      return (
        localStorage.getItem("theme") === "dark" ||
        (!localStorage.getItem("theme") &&
          window.matchMedia("(prefers-color-scheme: dark)").matches)
      );
    }
    return false;
  });
  const location = useLocation();
  const navigate = useNavigate();
  const { user, logout } = useAuth();

  const toggleDark = () => {
    const newDark = !dark;
    setDark(newDark);
    localStorage.setItem("theme", newDark ? "dark" : "light");
    document.documentElement.classList.toggle("dark", newDark);
  };

  // Init dark mode
  if (typeof document !== "undefined") {
    document.documentElement.classList.toggle("dark", dark);
  }

  // Close the drawer whenever the route changes, and scroll to the top so a
  // new page never opens half-way down the previous one.
  useEffect(() => {
    setSidebarOpen(false);
    setSearch("");
  }, [location.pathname]);

  const handleLogout = async () => {
    await logout();
    navigate("/login");
  };

  const onSearch = (event: React.FormEvent) => {
    event.preventDefault();
    const term = search.trim();
    if (!term) return;
    navigate(`/contacts?search=${encodeURIComponent(term)}`);
  };

  const isActive = (to: string) =>
    location.pathname === to || location.pathname.startsWith(`${to}/`);

  const groups = useMemo(
    () => [
      { title: "Start here", items: OVERVIEW },
      { title: "Audience & sending", items: WORK },
      { title: "Channels", items: CHANNELS },
      { title: "Automation", items: AUTOMATION },
      { title: "Content & insights", items: CONTENT },
    ],
    []
  );

  const setupBadge = (to: string) =>
    to === "/setup" && setupBlocking ? <span className="badge-red">{setupBlocking}</span> : null;

  return (
    <div className="min-h-[100dvh] flex bg-canvas dark:bg-canvas-dark">
      {/* Mobile sidebar backdrop */}
      {sidebarOpen && (
        <div
          className="fixed inset-0 z-40 bg-gray-900/50 backdrop-blur-sm lg:hidden animate-fade-in"
          onClick={() => setSidebarOpen(false)}
        />
      )}

      {/* Sidebar */}
      <aside
        className={`
          fixed top-0 left-0 z-50 h-full w-72 bg-white dark:bg-gray-800 border-r border-gray-200 dark:border-gray-700/70
          transform transition-transform duration-200 ease-gentle flex flex-col
          lg:translate-x-0 lg:static lg:z-auto
          ${sidebarOpen ? "translate-x-0 shadow-pop" : "-translate-x-full"}
        `}
      >
        <div className="flex items-center justify-between h-16 px-4 border-b border-gray-100 dark:border-gray-700/70 shrink-0">
          <Link to="/dashboard" className="flex items-center gap-2.5 group">
            <BrandMark className="w-9 h-9 shrink-0 rounded-xl transition-transform duration-200 ease-gentle group-hover:scale-105" />
            <span className="font-bold text-lg text-gray-900 dark:text-white tracking-tight">
              SMS SENDER
            </span>
          </Link>
          <button
            className="lg:hidden p-2 rounded-lg text-gray-500 hover:bg-gray-100 dark:hover:bg-gray-700"
            onClick={() => setSidebarOpen(false)}
            aria-label="Close menu"
          >
            <X size={20} />
          </button>
        </div>

        <nav className="p-3 space-y-4 overflow-y-auto flex-1 pb-tabbar lg:pb-3">
          {groups.map((group) => (
            <div key={group.title}>
              <p className="px-3 pb-1.5 text-[11px] font-semibold uppercase tracking-wider text-gray-400">
                {group.title}
              </p>
              <div className="space-y-0.5">
                {group.items.map((item) => (
                  <Link
                    key={item.to}
                    to={item.to}
                    className={`
                      flex items-center gap-3 px-3 py-2.5 rounded-xl text-sm font-medium
                      transition-all duration-150 ease-gentle
                      ${
                        isActive(item.to)
                          ? "bg-primary-50 dark:bg-primary-950/40 text-primary-700 dark:text-primary-300 shadow-sm"
                          : "text-gray-600 dark:text-gray-400 hover:bg-gray-100 dark:hover:bg-gray-700/60 hover:text-gray-900 dark:hover:text-gray-100"
                      }
                    `}
                  >
                    <item.icon size={18} className="shrink-0" />
                    <span className="flex-1">{item.label}</span>
                    {setupBadge(item.to)}
                  </Link>
                ))}
              </div>
            </div>
          ))}
        </nav>

        <div className="p-3 border-t border-gray-100 dark:border-gray-700/70 shrink-0 hidden lg:block">
          <div className="rounded-xl bg-gray-50 dark:bg-gray-900/50 p-3 flex items-center gap-3">
            <span className="w-9 h-9 rounded-full brand-gradient text-white text-xs font-semibold flex items-center justify-center shrink-0">
              {(user?.display_name || user?.username || "A").slice(0, 1).toUpperCase()}
            </span>
            <div className="min-w-0">
              <p className="text-sm font-medium text-gray-800 dark:text-gray-100 truncate">
                {user?.display_name || user?.username || "Administrator"}
              </p>
              <p className="text-[11px] text-gray-400">Signed in</p>
            </div>
          </div>
        </div>
      </aside>

      {/* Main content */}
      <div className="flex-1 flex flex-col min-w-0">
        {/* Top header */}
        <header className="sticky top-0 z-30 h-16 bg-white/90 dark:bg-gray-800/90 backdrop-blur border-b border-gray-200 dark:border-gray-700/70 flex items-center gap-2 px-3 sm:px-4 lg:px-6 safe-top">
          <button
            className="lg:hidden p-2 rounded-xl text-gray-500 hover:bg-gray-100 dark:hover:bg-gray-700 transition-colors"
            onClick={() => setSidebarOpen(true)}
            aria-label="Open menu"
          >
            <Menu size={20} />
          </button>

          <form onSubmit={onSearch} className="flex-1 max-w-md hidden sm:block">
            <div className="relative">
              <Search
                size={16}
                className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-400"
              />
              <input
                ref={searchRef}
                type="search"
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                placeholder="Search contacts…"
                aria-label="Search contacts"
                className="input pl-9 py-2"
              />
            </div>
          </form>

          <div className="flex-1 sm:hidden" />

          <div className="flex items-center gap-1 sm:gap-2 shrink-0">
            {/* One switch for the whole app: SMS or Email. */}
            <div className="hidden md:block mr-1">
              <ChannelSwitch navigateManagers />
            </div>
            <Link
              to="/email-inbox"
              className="p-2 rounded-xl text-gray-500 hover:bg-gray-100 dark:hover:bg-gray-700 transition-colors md:hidden"
              aria-label="Email inbox"
            >
              <MailOpen size={18} />
            </Link>
            <NotificationBell />
            <button
              onClick={toggleDark}
              className="p-2 rounded-xl text-gray-500 hover:bg-gray-100 dark:hover:bg-gray-700 transition-colors"
              title="Toggle dark mode"
              aria-label="Toggle dark mode"
            >
              {dark ? <Sun size={18} /> : <Moon size={18} />}
            </button>
            <button
              onClick={handleLogout}
              className="p-2 rounded-xl text-gray-500 hover:bg-gray-100 dark:hover:bg-gray-700 transition-colors"
              title="Log out"
              aria-label="Log out"
            >
              <LogOut size={18} />
            </button>
          </div>
        </header>

        {/* Page content */}
        <main key={location.pathname} className="flex-1 p-3 sm:p-4 lg:p-6 overflow-auto page-enter pb-tabbar">
          <Outlet />
        </main>
      </div>

      {/* Thumb bar — phones and small tablets only. */}
      <nav
        className="fixed bottom-0 left-0 right-0 z-40 lg:hidden bg-white/95 dark:bg-gray-800/95 backdrop-blur border-t border-gray-200 dark:border-gray-700/70 safe-bottom"
        aria-label="Primary"
      >
        <div className="grid grid-cols-6">
          {TAB_BAR.map((item) => {
            const active = isActive(item.to);
            return (
              <Link
                key={item.to}
                to={item.to}
                className={`flex flex-col items-center justify-center gap-0.5 py-2 min-h-[56px] transition-colors ${
                  active
                    ? "text-primary-600 dark:text-primary-400"
                    : "text-gray-500 dark:text-gray-400 hover:text-gray-800 dark:hover:text-gray-200"
                }`}
              >
                <span
                  className={`px-3 py-1 rounded-full transition-all duration-200 ease-gentle ${
                    active ? "bg-primary-50 dark:bg-primary-950/50" : ""
                  }`}
                >
                  <item.icon size={19} />
                </span>
                <span className="text-[10px] font-medium">{item.label}</span>
              </Link>
            );
          })}
          <button
            onClick={() => setSidebarOpen(true)}
            className="flex flex-col items-center justify-center gap-0.5 py-2 min-h-[56px] text-gray-500 dark:text-gray-400 hover:text-gray-800 dark:hover:text-gray-200 transition-colors"
          >
            <span className="px-3 py-1">
              <MoreHorizontal size={19} />
            </span>
            <span className="text-[10px] font-medium">More</span>
          </button>
        </div>
      </nav>
    </div>
  );
}
