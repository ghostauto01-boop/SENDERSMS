import { useState } from "react";
import { Link, useLocation, Outlet, useNavigate } from "react-router-dom";
import { useAuth } from "../hooks/useAuth";
import {
  LayoutDashboard, Users, List, Megaphone, GitBranch,
  Inbox, Send, Clock, FileText, BarChart3, Settings,
  Moon, Sun, Menu, X, LogOut, Search,
} from "lucide-react";

const navItems = [
  { to: "/dashboard", label: "Dashboard", icon: LayoutDashboard },
  { to: "/send", label: "Send SMS", icon: Send },
  { to: "/contacts", label: "Contacts", icon: Users },
  { to: "/lists", label: "Lists", icon: List },
  { to: "/campaigns", label: "Campaigns", icon: Megaphone },
  { to: "/sequences", label: "Sequences", icon: GitBranch },
  { to: "/inbox", label: "Inbox", icon: Inbox },
  { to: "/follow-ups", label: "Follow-ups", icon: Clock },
  { to: "/templates", label: "Templates", icon: FileText },
  { to: "/analytics", label: "Analytics", icon: BarChart3 },
  { to: "/settings", label: "Settings", icon: Settings },
];

export default function MainLayout() {
  const [sidebarOpen, setSidebarOpen] = useState(false);
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

  const handleLogout = async () => {
    await logout();
    navigate("/login");
  };

  return (
    <div className="min-h-screen flex bg-gray-50 dark:bg-gray-900">
      {/* Mobile sidebar backdrop */}
      {sidebarOpen && (
        <div
          className="fixed inset-0 z-40 bg-black/50 lg:hidden"
          onClick={() => setSidebarOpen(false)}
        />
      )}

      {/* Sidebar */}
      <aside
        className={`
          fixed top-0 left-0 z-50 h-full w-64 bg-white dark:bg-gray-800 border-r border-gray-200 dark:border-gray-700
          transform transition-transform duration-200 ease-in-out
          lg:translate-x-0 lg:static lg:z-auto
          ${sidebarOpen ? "translate-x-0" : "-translate-x-full"}
        `}
      >
        <div className="flex items-center justify-between h-16 px-4 border-b border-gray-200 dark:border-gray-700">
          <Link to="/dashboard" className="flex items-center gap-2">
            <div className="w-8 h-8 bg-primary-600 rounded-lg flex items-center justify-center">
              <span className="text-white font-bold text-sm">S</span>
            </div>
            <span className="font-bold text-lg text-gray-900 dark:text-white">
              SMS SENDER
            </span>
          </Link>
          <button
            className="lg:hidden text-gray-500 hover:text-gray-700 dark:hover:text-gray-300"
            onClick={() => setSidebarOpen(false)}
          >
            <X size={20} />
          </button>
        </div>

        <nav className="p-3 space-y-1 overflow-y-auto h-[calc(100%-4rem)]">
          {navItems.map((item) => {
            const isActive = location.pathname.startsWith(item.to);
            return (
              <Link
                key={item.to}
                to={item.to}
                onClick={() => setSidebarOpen(false)}
                className={`
                  flex items-center gap-3 px-3 py-2.5 rounded-lg text-sm font-medium transition-colors
                  ${
                    isActive
                      ? "bg-primary-50 dark:bg-primary-900/30 text-primary-700 dark:text-primary-300"
                      : "text-gray-600 dark:text-gray-400 hover:bg-gray-100 dark:hover:bg-gray-700"
                  }
                `}
              >
                <item.icon size={18} />
                {item.label}
              </Link>
            );
          })}
        </nav>
      </aside>

      {/* Main content */}
      <div className="flex-1 flex flex-col min-h-screen">
        {/* Top header */}
        <header className="h-16 bg-white dark:bg-gray-800 border-b border-gray-200 dark:border-gray-700 flex items-center justify-between px-4 lg:px-6">
          <button
            className="lg:hidden text-gray-500 hover:text-gray-700 dark:hover:text-gray-300"
            onClick={() => setSidebarOpen(true)}
          >
            <Menu size={20} />
          </button>

          <div className="flex-1 max-w-md ml-4 hidden sm:block">
            <div className="relative">
              <Search
                size={16}
                className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-400"
              />
              <input
                type="text"
                placeholder="Search contacts, messages..."
                className="input pl-9 py-1.5 text-sm"
              />
            </div>
          </div>

          <div className="flex items-center gap-2">
            <button
              onClick={toggleDark}
              className="p-2 rounded-lg text-gray-500 hover:bg-gray-100 dark:hover:bg-gray-700"
              title="Toggle dark mode"
            >
              {dark ? <Sun size={18} /> : <Moon size={18} />}
            </button>
            <div className="flex items-center gap-2 ml-2 pl-2 border-l border-gray-200 dark:border-gray-700">
              <span className="text-sm text-gray-700 dark:text-gray-300 hidden sm:block">
                {user?.display_name || user?.username}
              </span>
              <button
                onClick={handleLogout}
                className="p-2 rounded-lg text-gray-500 hover:bg-gray-100 dark:hover:bg-gray-700"
                title="Logout"
              >
                <LogOut size={18} />
              </button>
            </div>
          </div>
        </header>

        {/* Page content */}
        <main className="flex-1 p-4 lg:p-6 overflow-auto">
          <Outlet />
        </main>
      </div>
    </div>
  );
}
