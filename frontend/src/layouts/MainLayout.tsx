import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useLocation, Outlet, useNavigate } from "react-router-dom";
import { useAuth } from "../hooks/useAuth";
import {
  LayoutDashboard, Users, List, GitBranch, Sparkles,
  Inbox, Send, Clock, FileText, BarChart3, Settings, MessageSquareReply,
  Zap, Menu, X, LogOut, Search, Braces, Repeat, Calendar, Target,
  Activity, Phone, Mail, MailOpen, GraduationCap, MoreHorizontal, ShieldCheck, Briefcase,
  ReceiptText, ArrowUpRight,
} from "lucide-react";
import BrandMark from "../components/BrandMark";
import ChannelSwitch from "../components/ChannelSwitch";
import NotificationBell from "../components/NotificationBell";
import guideApi from "../api/guide";

/**
 * The app shell — the "forest & parchment" workspace.
 *
 * On a desktop it is a white, softly rounded device frame floating on the warm
 * parchment canvas: a tinted navigation rail on the left, a barely-there grey
 * content surface on the right, and one sage pill marking the page you are on.
 * On a phone the rail becomes the familiar drawer and a thumb-reachable bottom
 * bar keeps the five all-day destinations one tap away.
 *
 * The design rules that matter here:
 *  * the active item is the ONLY coloured row in the rail;
 *  * the user chip in the header is the only "profile" surface, so pages can
 *    stay about their work;
 *  * every movement is short, springy and disabled for reduced-motion users.
 */

interface NavItem {
  to: string;
  label: string;
  icon: typeof Activity;
}

const MENU: NavItem[] = [
  { to: "/dashboard", label: "Dashboard", icon: LayoutDashboard },
  { to: "/invoices", label: "Invoices", icon: ReceiptText },
  { to: "/contacts", label: "Contacts", icon: Users },
  { to: "/send", label: "Send", icon: Send },
  { to: "/drafts", label: "Drafts", icon: FileText },
  { to: "/analytics", label: "Analytics", icon: BarChart3 },
];

const CHANNELS: NavItem[] = [
  { to: "/sms-manager", label: "SMS Manager", icon: Sparkles },
  { to: "/email-manager", label: "Email Manager", icon: Mail },
  { to: "/sms-inbox", label: "SMS Inbox", icon: Inbox },
  { to: "/email-inbox", label: "Email Inbox", icon: MailOpen },
];

const AUDIENCE: NavItem[] = [
  { to: "/conversations", label: "Conversations", icon: MessageSquareReply },
  { to: "/opportunities", label: "Opportunity", icon: Briefcase },
  { to: "/lists", label: "Lists", icon: List },
  { to: "/audiences", label: "Audiences", icon: Target },
  { to: "/calendar", label: "Calendar", icon: Calendar },
  { to: "/templates", label: "Templates", icon: FileText },
];

const AUTOMATION: NavItem[] = [
  { to: "/sequences", label: "Sequences", icon: GitBranch },
  { to: "/automations", label: "Automations", icon: Zap },
  { to: "/auto-reply", label: "Auto-Reply", icon: MessageSquareReply },
  { to: "/follow-ups", label: "Follow-ups", icon: Clock },
  { to: "/campaign-follow-ups", label: "Campaign follow-ups", icon: Repeat },
];

const TOOLS: NavItem[] = [
  { to: "/validator", label: "Validator", icon: ShieldCheck },
  { to: "/phone", label: "Phone", icon: Phone },
  { to: "/variables", label: "Variables", icon: Braces },
  { to: "/overview", label: "Overview", icon: Activity },
];

const GENERAL: NavItem[] = [
  { to: "/setup", label: "Setup guide", icon: GraduationCap },
  { to: "/settings", label: "Settings", icon: Settings },
];

//: Thumb-bar destinations. Email is a first-class channel now, so it earns a
//: permanent slot: on a phone the drawer is a two-tap detour away from the mail
//: people open twenty times a day. Seven columns still leaves every target
//: wider than the 44px a fingertip needs, even on a 320px screen.
const TAB_BAR: NavItem[] = [
  { to: "/dashboard", label: "Home", icon: LayoutDashboard },
  { to: "/send", label: "Send", icon: Send },
  { to: "/inbox", label: "SMS", icon: Inbox },
  { to: "/email-inbox", label: "Mail", icon: MailOpen },
  { to: "/invoices", label: "Invoices", icon: ReceiptText },
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

  const location = useLocation();
  const navigate = useNavigate();
  const { user, logout } = useAuth();

  // Close the drawer whenever the route changes, and scroll content to the top
  // so a new page never opens half-way down the previous one.
  useEffect(() => {
    setSidebarOpen(false);
    setSearch("");
    document.getElementById("workspace-main")?.scrollTo({ top: 0 });
  }, [location.pathname]);

  // ⌘K / Ctrl-K focuses search — the small keyboard courtesy people expect
  // from a desktop app.
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        searchRef.current?.focus();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

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

  /**
   * The sidebar highlights exactly one item: the most specific match.
   * /sms-inbox and /inbox are the same screen under two paths, so the longest
   * matching item wins and the other stays quiet.
   */
  const allItems = useMemo(
    () => [...MENU, ...CHANNELS, ...AUDIENCE, ...AUTOMATION, ...TOOLS, ...GENERAL],
    []
  );
  const activeItem = useMemo(() => {
    const aliases: Record<string, string> = { "/inbox": "/sms-inbox" };
    const path = aliases[location.pathname] || location.pathname;
    return (
      [...allItems.map((i) => ({ ...i, to: aliases[i.to] || i.to }))]
        .filter((item) => path === item.to || path.startsWith(`${item.to}/`))
        .sort((a, b) => b.to.length - a.to.length)[0]?.to ?? null
    );
  }, [location.pathname, location.search, allItems]);

  const isActive = (to: string) => to === activeItem;

  /**
   * Some screens are an app of their own and must own the whole window.
   *
   * The email inbox is Gmail-shaped: a toolbar, a folder rail, a thread list and
   * a reading pane with their own scroll areas. Giving it the shell's page
   * padding (and its centred `max-w-7xl` column) squeezed the reading pane into a
   * letterbox — the exact bug people reported as "I cannot read an email full
   * screen". Full-bleed hands it every pixel under the header instead.
   */
  const fullBleed = location.pathname === "/email-inbox";

  const groups = useMemo(
    () => [
      { title: "Menu", items: MENU },
      { title: "Channels", items: CHANNELS },
      { title: "Audience & content", items: AUDIENCE },
      { title: "Automation", items: AUTOMATION },
      { title: "Tools", items: TOOLS },
      { title: "General", items: GENERAL },
    ],
    []
  );

  const setupBadge = (to: string) =>
    to === "/setup" && setupBlocking ? <span className="nav-badge">{setupBlocking}</span> : null;

  const initials = (user?.display_name || user?.username || "A").slice(0, 1).toUpperCase();
  const displayName = user?.display_name || user?.username || "Administrator";

  const nav = (
    <>
      <div className="flex items-center justify-between px-5 pt-6 pb-5 shrink-0">
        <Link to="/dashboard" className="flex items-center gap-2.5 group">
          <BrandMark className="w-9 h-9 shrink-0 rounded-[13px] shadow-sm transition-transform duration-300 ease-spring group-hover:scale-105" />
          <span className="font-semibold text-[19px] text-primary-950 dark:text-white tracking-[-0.04em] lowercase">
            sendify
          </span>
        </Link>
        <button
          className="lg:hidden icon-btn"
          onClick={() => setSidebarOpen(false)}
          aria-label="Close menu"
        >
          <X size={17} />
        </button>
      </div>

      <nav className="px-3 pb-2 overflow-y-auto flex-1 scrollbar-none space-y-5" aria-label="Main">
        {groups.map((group) => (
          <div key={group.title}>
            <p className="px-3.5 pb-2 text-[10px] font-semibold uppercase tracking-[0.18em] text-gray-400/90 dark:text-gray-500">
              {group.title}
            </p>
            <div className="space-y-0.5">
              {group.items.map((item) => {
                const active = isActive(item.to);
                return (
                  <Link
                    key={item.to}
                    to={item.to}
                    aria-current={active ? "page" : undefined}
                    className={`nav-item ${active ? "nav-item-active" : ""}`}
                  >
                    <item.icon
                      size={17}
                      strokeWidth={active ? 2.1 : 1.8}
                      className={active ? "text-primary-900 dark:text-primary-200" : ""}
                    />
                    <span className="flex-1 truncate">{item.label}</span>
                    {setupBadge(item.to)}
                  </Link>
                );
              })}
            </div>
          </div>
        ))}
      </nav>

      <div className="p-3.5 pt-2 shrink-0 space-y-2">
        <button
          onClick={handleLogout}
          className="nav-item w-full text-gray-500 hover:text-danger-600"
        >
          <LogOut size={17} strokeWidth={1.8} />
          <span>Log out</span>
          <ArrowUpRight size={14} className="ml-auto opacity-40" />
        </button>
      </div>
    </>
  );

  return (
    <div className="min-h-[100dvh] bg-canvas dark:bg-canvas-dark lg:h-[100dvh] lg:p-5 xl:p-6 lg:overflow-hidden">
      <div className="workspace-frame mx-auto flex w-full max-w-[1720px] min-h-[100dvh] lg:h-full lg:min-h-0">
        {/* Mobile sidebar backdrop */}
        {sidebarOpen && (
          <div
            className="fixed inset-0 z-40 bg-primary-950/35 backdrop-blur-sm lg:hidden animate-fade-in"
            onClick={() => setSidebarOpen(false)}
          />
        )}

        {/* Navigation rail */}
        <aside
          className={`
            workspace-sidebar fixed top-0 left-0 z-50 h-[100dvh] w-[276px] flex flex-col shrink-0
            transform transition-transform duration-300 ease-gentle
            lg:static lg:translate-x-0 lg:h-full lg:z-auto
            ${sidebarOpen ? "translate-x-0 shadow-pop" : "-translate-x-full"}
          `}
        >
          {nav}
        </aside>

        {/* Workspace */}
        <div className="workspace-content flex-1 flex flex-col min-w-0 lg:rounded-r-[2.5rem]">
          <header className="shrink-0 flex items-center gap-2 px-3 sm:px-4 lg:px-6 pt-3 lg:pt-4 safe-top">
            <button
              className="lg:hidden icon-btn"
              onClick={() => setSidebarOpen(true)}
              aria-label="Open menu"
            >
              <Menu size={18} />
            </button>

            <Link to="/dashboard" className="lg:hidden flex items-center gap-2 mr-1">
              <BrandMark className="w-8 h-8 rounded-xl" />
            </Link>

            <form onSubmit={onSearch} className="hidden md:block flex-1 max-w-[320px]">
              <div className="search-pill">
                <Search size={16} className="text-gray-400 shrink-0" />
                <input
                  ref={searchRef}
                  type="search"
                  value={search}
                  onChange={(e) => setSearch(e.target.value)}
                  placeholder="Search contacts, phone, email…"
                  aria-label="Search contacts"
                />
                <kbd className="hidden lg:inline-flex items-center gap-1 rounded-md border border-gray-200 bg-white px-1.5 py-0.5 text-[10px] text-gray-400">
                  ⌘K
                </kbd>
              </div>
            </form>

            <div className="flex-1 md:hidden" />

            <div className="ml-auto flex items-center gap-1.5 sm:gap-2 shrink-0">
              <div className="hidden md:block">
                <ChannelSwitch navigateManagers={location.pathname !== "/overview"} />
              </div>
              <NotificationBell />
              <Link to="/settings" className="user-chip" aria-label="Account settings">
                <span className="avatar w-8 h-8 text-xs">{initials}</span>
                <span className="hidden sm:block leading-tight pr-0.5">
                  <span className="block text-[12.5px] font-semibold text-gray-800 dark:text-gray-100 max-w-[130px] truncate">
                    {displayName}
                  </span>
                  <span className="block text-[10px] text-gray-400 max-w-[130px] truncate">
                    Operator account
                  </span>
                </span>
              </Link>
              <button
                onClick={handleLogout}
                className="icon-btn hidden lg:flex"
                title="Log out"
                aria-label="Log out"
              >
                <LogOut size={16} />
              </button>
            </div>
          </header>

          {/* Page content — the only scroll container on desktop. */}
          <main
            id="workspace-main"
            key={location.pathname}
            className={
              fullBleed
                ? // No padding, no scroll container of its own: the page manages
                  // its panes (and their scrollbars) itself.
                  "flex-1 min-w-0 flex flex-col page-enter overflow-hidden"
                : "flex-1 min-w-0 px-3 sm:px-4 lg:px-6 pt-4 lg:pt-5 pb-tabbar page-enter lg:overflow-y-auto wa-scrollbar"
            }
          >
            <Outlet />
          </main>
        </div>
      </div>

      {/* Thumb bar — phones and small tablets only. */}
      <nav
        className="fixed bottom-0 left-0 right-0 z-40 lg:hidden bg-white/95 dark:bg-gray-900/95 backdrop-blur-xl border-t border-gray-200/70 dark:border-gray-700/70 safe-bottom"
        aria-label="Primary"
      >
        <div className="grid grid-cols-7">
          {TAB_BAR.map((item) => {
            const aliases: Record<string, string> = { "/inbox": "/sms-inbox" };
            const path = aliases[location.pathname] || location.pathname;
            const active = path === aliases[item.to] || path.startsWith(`${item.to}/`);
            return (
              <Link
                key={item.to}
                to={item.to}
                className={`flex flex-col items-center justify-center gap-0.5 py-2 min-h-[58px] transition-colors duration-200 ${
                  active
                    ? "text-primary-900 dark:text-primary-300"
                    : "text-gray-400 dark:text-gray-500 hover:text-gray-800 dark:hover:text-gray-200"
                }`}
              >
                <span
                  className={`px-3 py-1 rounded-full transition-all duration-300 ease-spring ${
                    active ? "bg-primary-100 dark:bg-primary-950/60 scale-105" : ""
                  }`}
                >
                  <item.icon size={18} strokeWidth={active ? 2.2 : 1.8} />
                </span>
                <span className="text-[10px] font-medium leading-none truncate max-w-full px-0.5">
                  {item.label}
                </span>
              </Link>
            );
          })}
          <button
            onClick={() => setSidebarOpen(true)}
            className="flex flex-col items-center justify-center gap-0.5 py-2 min-h-[58px] text-gray-400 dark:text-gray-500 hover:text-gray-800 dark:hover:text-gray-200 transition-colors duration-200"
          >
            <span className="px-3 py-1">
              <MoreHorizontal size={18} strokeWidth={1.8} />
            </span>
            <span className="text-[10px] font-medium leading-none">More</span>
          </button>
        </div>
      </nav>
    </div>
  );
}
