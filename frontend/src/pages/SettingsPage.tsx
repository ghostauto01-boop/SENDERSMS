import { useState, useEffect } from "react";
import api from "../api/client";
import AgentModelSettings from "../components/agent/AgentModelSettings";
import { useAuth } from "../hooks/useAuth";
import { NotificationProviderSettings } from "../types";
import toast from "react-hot-toast";
import {
  Activity,
  Bell,
  CheckCircle,
  CreditCard,
  Eye,
  EyeOff,
  Inbox,
  Lock,
  Mail,
  Phone,
  Plus,
  RefreshCw,
  Shield,
  ShieldCheck,
  Sparkles,
  TestTube,
  Trash2,
  Webhook,
  Wifi,
  XCircle,
} from "lucide-react";
import { Link } from "react-router-dom";
import emailApi, { EmailAccount } from "../api/email";
import McpConnectors from "../components/McpConnectors";
import GoogleOAuthSetupCard from "../components/GoogleOAuthSetupCard";
import MailboxRepliesTab from "../components/MailboxRepliesTab";
import PaystackSettingsCard from "../components/invoices/PaystackSettingsCard";
import { ThemeToggle } from "../components/ThemeToggle";
import {
  PALETTE_LIST,
  THEME_PRESETS,
  useTheme,
  type DensityMode,
  type FontFamilyId,
  type PaletteId,
  type RadiusMode,
  type ThemeMode,
} from "../hooks/useTheme";
import { Check, Laptop, Moon, Palette, Smartphone, Sun } from "lucide-react";

type Tab =
  | "appearance"
  | "gateway"
  | "calls"
  | "notifications"
  | "compliance"
  | "access"
  | "email"
  | "ai"
  | "gmail"
  | "payments";

const TAB_IDS: Tab[] = [
  "appearance", "access", "gateway", "payments", "email", "gmail", "ai", "calls", "notifications",
  "compliance",
];

/**
 * Which tab to open when the page is reached with a query string.
 *
 * `/settings?tab=gmail` is where the setup guide and the "Google sign-in is not
 * configured" warnings send the operator, so the tab they were told about is the
 * tab they land on. Read once, the same way the Email Manager reads
 * `?section=`: the tabs are then plain local state, and switching one does not
 * rewrite the URL under the browser's back button.
 */
function tabFromQuery(): Tab {
  const wanted = new URLSearchParams(window.location.search).get("tab") as Tab | null;
  return wanted && TAB_IDS.includes(wanted) ? wanted : "gateway";
}

export default function SettingsPage() {
  const [tab, setTab] = useState<Tab>(tabFromQuery);
  const [notifs, setNotifs] = useState<NotificationProviderSettings[]>([]);
  const [comp, setComp] = useState<Record<string, string>>({});
  const [loading, setLoading] = useState(true);

  useEffect(() => { load(); }, []);
  const load = async () => {
    setLoading(true);
    try {
      const [n, c] = await Promise.all([
        api.get("/settings/notifications"), api.get("/settings/compliance"),
      ]);
      setNotifs(n.data.providers); setComp(c.data);
    } catch {} finally { setLoading(false); }
  };

  const tabs = [
    { id: "appearance" as Tab, label: "Appearance", icon: Sun },
    { id: "access" as Tab, label: "Site access", icon: Lock },
    { id: "gateway" as Tab, label: "SMS Gateway", icon: Wifi },
    { id: "payments" as Tab, label: "Payments (Paystack)", icon: CreditCard },
    { id: "email" as Tab, label: "Email (Brevo)", icon: Mail },
    { id: "gmail" as Tab, label: "Gmail replies", icon: Inbox },
    { id: "ai" as Tab, label: "AI (MCP)", icon: Sparkles },
    { id: "calls" as Tab, label: "Calls", icon: Phone },
    { id: "notifications" as Tab, label: "Notifications", icon: Bell },
    { id: "compliance" as Tab, label: "Compliance", icon: Shield },
  ];

  return (
    <div className="space-y-4">
      <div>
        <p className="eyebrow">Workspace</p>
        <h1 className="page-title mt-1">Settings</h1>
        <p className="page-subtitle">Gateway, channels, payments, notifications and access — all in one place.</p>
      </div>
      <div className="flex gap-1.5 overflow-x-auto scrollbar-none pb-1 -mx-1 px-1">{tabs.map(t => (<button key={t.id} onClick={() => setTab(t.id)}
        className={`chip shrink-0 ${tab===t.id ? "chip-active" : ""}`}><t.icon size={13}/>{t.label}</button>))}</div>
      {loading ? (<div className="card p-6"><div className="skeleton h-8 w-48 mb-4"/><div className="space-y-3">{[...Array(3)].map((_,i)=>(<div key={i} className="skeleton h-10 w-full"/>))}</div></div>)
      : (<>{tab==="appearance"&&<AppearanceTab />}{tab==="access"&&<AccessTab />}{tab==="gateway"&&<GatewayTab />}{tab==="payments"&&<PaymentsTab />}{tab==="calls"&&<CallsTab />}{tab==="email"&&<EmailTab />}{tab==="gmail"&&<GmailTab />}{tab==="ai"&&<McpTab />}{tab==="notifications"&&<NotifsTab provs={notifs} onUpdate={load}/>}{tab==="compliance"&&<CompTab s={comp} onUpdate={load}/>}</>)}
    </div>);
}

/**
 * APPEARANCE
 *
 * Light and dark are the same design, not a filter: every colour token has a
 * counterpart in the tailwind config. The choice is stored locally and applied
 * before the first paint, so switching is instant and never flashes.
 */
function AppearanceTab() {
  const {
    theme,
    mode,
    setMode,
    palette,
    setPalette,
    density,
    setDensity,
    radius,
    setRadius,
    font,
    setFont,
    applyPreset,
    resetAppearance,
  } = useTheme();
  const [syncing, setSyncing] = useState(false);

  const modeCards: Array<{
    id: ThemeMode;
    label: string;
    blurb: string;
    icon: typeof Sun;
    swatch: string[];
  }> = [
    {
      id: "light",
      label: "Parchment light",
      blurb: "Crisp daylight surfaces with rich editorial contrast.",
      icon: Sun,
      swatch: ["#f6f5f1", "#ffffff", "#123c2b", "#dfe9e3"],
    },
    {
      id: "dark",
      label: "Forest night",
      blurb: "Deep low-glare surfaces tuned for late shifts and OLED displays.",
      icon: Moon,
      swatch: ["#0c1712", "#16211b", "#8fc7a8", "#243a2e"],
    },
    {
      id: "system",
      label: "System automatic",
      blurb: "Follows your OS light/dark schedule seamlessly.",
      icon: Laptop,
      swatch: ["#f6f5f1", "#16211b", "#2563eb", "#8fc7a8"],
    },
  ];

  const syncWorkspaceAppearance = async () => {
    setSyncing(true);
    try {
      await api.put("/settings/appearance", { mode, palette, density, radius, font });
      toast.success("Workspace appearance saved across devices");
    } catch (e: any) {
      toast.error(e?.response?.data?.detail || "Appearance applied locally");
    } finally {
      setSyncing(false);
    }
  };

  return (
    <div className="space-y-5 max-w-5xl">
      {/* Header & Mode Switcher */}
      <div className="card p-5 sm:p-6 space-y-5">
        <div className="flex flex-col sm:flex-row sm:items-center gap-4 sm:justify-between">
          <div>
            <h2 className="text-base font-semibold flex items-center gap-2">
              <Palette size={17} className="text-primary-700 dark:text-accent-400" />
              Appearance &amp; Theme Studio
            </h2>
            <p className="text-xs text-gray-500 dark:text-gray-400 mt-1 max-w-xl">
              Customize light/dark mode, professional color palettes, layout density, corner
              geometry, and typography. Changes apply zero-flash across every screen.
            </p>
          </div>
          <div className="flex items-center gap-2 self-start sm:self-auto">
            <ThemeToggle />
            <button
              type="button"
              onClick={resetAppearance}
              className="btn-secondary btn-sm text-xs"
            >
              Reset
            </button>
            <button
              type="button"
              onClick={syncWorkspaceAppearance}
              disabled={syncing}
              className="btn-primary btn-sm text-xs"
            >
              {syncing ? "Saving…" : "Save Default"}
            </button>
          </div>
        </div>

        {/* Light / Dark / System cards */}
        <div>
          <p className="text-[11px] font-semibold uppercase tracking-wider text-gray-500 dark:text-gray-400 mb-2.5">
            Surface Mode
          </p>
          <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
            {modeCards.map((card) => (
              <ThemePreviewCard
                key={card.id}
                active={mode === card.id}
                label={card.label}
                blurb={card.blurb}
                Icon={card.icon}
                swatch={card.swatch}
                onSelect={() => setMode(card.id)}
              />
            ))}
          </div>
        </div>

        {/* Curated One-Click Executive Presets */}
        <div>
          <p className="text-[11px] font-semibold uppercase tracking-wider text-gray-500 dark:text-gray-400 mb-2.5">
            Curated Studio Presets
          </p>
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-2.5">
            {THEME_PRESETS.map((preset) => {
              const isCurrent =
                mode === preset.mode &&
                palette === preset.palette &&
                density === preset.density &&
                radius === preset.radius &&
                font === preset.font;
              return (
                <button
                  key={preset.id}
                  type="button"
                  onClick={() => {
                    applyPreset(preset);
                    toast.success(`Applied "${preset.name}" theme preset`);
                  }}
                  className={`text-left rounded-xl border p-3 transition-all ${
                    isCurrent
                      ? "border-primary-700 dark:border-accent-400 bg-primary-50/50 dark:bg-primary-900/20 ring-1 ring-primary-700/20"
                      : "border-gray-200 dark:border-gray-700 hover:border-gray-300 dark:hover:border-gray-600"
                  }`}
                >
                  <div className="flex items-center justify-between gap-2">
                    <span className="text-xs font-semibold text-gray-900 dark:text-gray-100">
                      {preset.name}
                    </span>
                    {isCurrent && <span className="badge-green text-[10px]">Active</span>}
                  </div>
                  <p className="text-[11px] text-gray-500 dark:text-gray-400 mt-0.5">
                    {preset.description}
                  </p>
                </button>
              );
            })}
          </div>
        </div>
      </div>

      {/* 10 Professional Color Palettes */}
      <div className="card p-5 sm:p-6 space-y-4">
        <div className="flex items-center justify-between gap-2">
          <div>
            <h3 className="text-sm font-semibold text-gray-900 dark:text-gray-100">
              Professional Color Palettes ({PALETTE_LIST.length})
            </h3>
            <p className="text-xs text-gray-500 dark:text-gray-400 mt-0.5">
              Harmonized semantic primary, surface, border, and dark-mode accent tokens.
            </p>
          </div>
          <span className="badge-blue capitalize">{palette}</span>
        </div>

        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-5 gap-3">
          {PALETTE_LIST.map((p) => {
            const active = palette === p.id;
            return (
              <button
                key={p.id}
                type="button"
                onClick={() => setPalette(p.id as PaletteId)}
                aria-pressed={active}
                data-testid={`palette-${p.id}`}
                className={`text-left rounded-2xl border p-3 transition-all ${
                  active
                    ? "border-primary-800 dark:border-accent-400 ring-2 ring-primary-700/20 dark:ring-accent-400/30 bg-primary-50/40 dark:bg-primary-900/20"
                    : "border-gray-200 dark:border-gray-700 hover:border-gray-300 dark:hover:border-gray-600"
                }`}
              >
                <div className="flex items-center gap-1 h-10 rounded-xl overflow-hidden border border-black/5 dark:border-white/10">
                  {p.swatches.map((hex, i) => (
                    <span key={i} className="h-full flex-1" style={{ backgroundColor: hex }} />
                  ))}
                </div>
                <div className="flex items-center justify-between gap-1 mt-2.5">
                  <span className="text-xs font-semibold text-gray-900 dark:text-gray-100 truncate">
                    {p.name}
                  </span>
                  {active && (
                    <Check size={13} className="text-primary-700 dark:text-accent-400 shrink-0" />
                  )}
                </div>
                <p className="text-[10.5px] text-gray-500 dark:text-gray-400 mt-0.5 leading-snug line-clamp-2">
                  {p.tagline}
                </p>
              </button>
            );
          })}
        </div>
      </div>

      {/* Density, Corner Geometry & Typography */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        <div className="card p-4 space-y-2.5">
          <p className="text-xs font-semibold text-gray-900 dark:text-gray-100">Interface Density</p>
          <p className="text-[11px] text-gray-500 dark:text-gray-400">
            Adjust spacing for high-density operator tables or relaxed executive review.
          </p>
          <div className="grid grid-cols-3 gap-1.5 pt-1">
            {(["compact", "comfortable", "spacious"] as DensityMode[]).map((d) => (
              <button
                key={d}
                type="button"
                onClick={() => setDensity(d)}
                className={`px-2.5 py-1.5 rounded-lg text-xs font-medium capitalize border transition ${
                  density === d
                    ? "border-primary-800 dark:border-accent-400 bg-primary-50 dark:bg-primary-900/30 text-primary-900 dark:text-accent-300"
                    : "border-gray-200 dark:border-gray-700 text-gray-600 dark:text-gray-300"
                }`}
              >
                {d}
              </button>
            ))}
          </div>
        </div>

        <div className="card p-4 space-y-2.5">
          <p className="text-xs font-semibold text-gray-900 dark:text-gray-100">Corner Geometry</p>
          <p className="text-[11px] text-gray-500 dark:text-gray-400">
            Architectural sharp edges, balanced rounded cards, or organic soft curves.
          </p>
          <div className="grid grid-cols-3 gap-1.5 pt-1">
            {(["sharp", "rounded", "soft"] as RadiusMode[]).map((r) => (
              <button
                key={r}
                type="button"
                onClick={() => setRadius(r)}
                className={`px-2.5 py-1.5 rounded-lg text-xs font-medium capitalize border transition ${
                  radius === r
                    ? "border-primary-800 dark:border-accent-400 bg-primary-50 dark:bg-primary-900/30 text-primary-900 dark:text-accent-300"
                    : "border-gray-200 dark:border-gray-700 text-gray-600 dark:text-gray-300"
                }`}
              >
                {r}
              </button>
            ))}
          </div>
        </div>

        <div className="card p-4 space-y-2.5">
          <p className="text-xs font-semibold text-gray-900 dark:text-gray-100">Typography</p>
          <p className="text-[11px] text-gray-500 dark:text-gray-400">
            Select geometric sans, clean UI sans, editorial serif, or technical monospace.
          </p>
          <div className="grid grid-cols-2 gap-1.5 pt-1">
            {(
              [
                { id: "default", label: "Plus Jakarta" },
                { id: "inter", label: "Inter Clean" },
                { id: "editorial", label: "Editorial" },
                { id: "mono", label: "Operator Mono" },
              ] as Array<{ id: FontFamilyId; label: string }>
            ).map((f) => (
              <button
                key={f.id}
                type="button"
                onClick={() => setFont(f.id)}
                className={`px-2.5 py-1.5 rounded-lg text-xs font-medium border transition ${
                  font === f.id
                    ? "border-primary-800 dark:border-accent-400 bg-primary-50 dark:bg-primary-900/30 text-primary-900 dark:text-accent-300"
                    : "border-gray-200 dark:border-gray-700 text-gray-600 dark:text-gray-300"
                }`}
              >
                {f.label}
              </button>
            ))}
          </div>
        </div>
      </div>

      {/* Live Preview Strip */}
      <div className="card p-5 space-y-3">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div>
            <p className="eyebrow">Live Preview</p>
            <h4 className="text-sm font-semibold mt-0.5">
              Active Theme: <span className="capitalize">{palette}</span> ·{" "}
              <span className="capitalize">{theme}</span> ({mode})
            </h4>
          </div>
          <div className="flex items-center gap-2">
            <span className="badge-green">Delivered 98.4%</span>
            <span className="badge-blue">OpenRouter Ready</span>
            <button type="button" className="btn-primary btn-sm">
              Primary Action
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

function ThemePreviewCard({
  active, label, blurb, Icon, swatch, onSelect,
}: {
  active: boolean;
  label: string;
  blurb: string;
  Icon: typeof Sun;
  swatch: string[];
  onSelect: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onSelect}
      aria-pressed={active}
      className={`text-left rounded-2xl border p-3 transition-all duration-200 ease-gentle ${
        active
          ? "border-primary-900 dark:border-accent-400 ring-2 ring-primary-900/15 dark:ring-accent-400/30"
          : "border-gray-200 dark:border-gray-700 hover:border-gray-300 dark:hover:border-gray-600"
      }`}
    >
      <div className="flex items-center gap-1.5 overflow-hidden rounded-xl h-16 border border-gray-200/70 dark:border-gray-700/60">
        {swatch.map((colour, index) => (
          <span key={index} className="h-full" style={{ background: colour, flex: index === 2 ? 0.7 : 1 }} />
        ))}
      </div>
      <div className="flex items-center gap-1.5 mt-2.5">
        <Icon size={13} className="text-primary-700 dark:text-primary-300" />
        <span className="text-[12.5px] font-semibold">{label}</span>
        {active && <span className="badge-green ml-auto">Active</span>}
      </div>
      <p className="text-[11px] text-gray-500 mt-0.5 leading-snug">{blurb}</p>
    </button>
  );
}

function PaymentsTab() {
  return (
    <div className="space-y-4">
      <div className="rounded-2xl bg-primary-50 dark:bg-primary-950/40 px-4 py-3 text-xs text-primary-900 dark:text-primary-200">
        <p className="font-semibold mb-1">This is where the invoice studio gets paid.</p>
        <p>
          Paste your Paystack public and secret keys below, save, then press
          “Test Paystack connection”. The same fields are reachable from Invoices → Paystack.
          Your secret key is encrypted on the server and never sent back to the browser.
        </p>
      </div>
      <PaystackSettingsCard />
    </div>
  );
}

function AccessTab() {
  const { noteAccess } = useAuth();
  const [passwordSet, setPasswordSet] = useState(false);
  const [password, setPassword] = useState("");
  const [show, setShow] = useState(false);
  const [saving, setSaving] = useState(false);
  const [ready, setReady] = useState(false);

  useEffect(() => {
    api.get("/settings/access")
      .then(({ data }) => {
        setPasswordSet(!!data.password_set);
      })
      .catch(() => toast.error("Could not load site access"))
      .finally(() => setReady(true));
  }, []);

  const save = async () => {
    if (!password.trim() && !passwordSet) {
      toast.error("Type the extra password you want to sign in with");
      return;
    }
    setSaving(true);
    try {
      const { data } = await api.put("/settings/access", {
        password_required: true,
        password: password.trim() || undefined,
      });
      const hadPassword = passwordSet;
      setPasswordSet(!!data.password_set);
      setPassword("");
      noteAccess(true);
      toast.success(
        hadPassword
          ? "Extra login password updated — it signs you in as well."
          : "Extra login password saved — it signs you in as well."
      );
    } catch (e: any) {
      toast.error(e?.response?.data?.detail || "Could not save");
    } finally {
      setSaving(false);
    }
  };

  if (!ready) {
    return <div className="card p-6 max-w-lg"><div className="skeleton h-8 w-48" /></div>;
  }

  return (
    <div className="card p-6 space-y-4 max-w-lg">
      <h2 className="text-lg font-semibold">Site access</h2>
      <div className="p-3 rounded-xl text-sm bg-primary-50 text-primary-800 dark:bg-primary-950/40 dark:text-primary-200 space-y-1">
        <p className="font-medium flex items-center gap-2">
          <ShieldCheck size={15} /> The login screen asks for the admin password
        </p>
        <p className="text-xs">
          That password is <code>ADMIN_PASSWORD</code> in the server environment (the shipped
          default is <code>12345678</code>). Change it there — the stored hash follows the
          environment on the next sign-in. The password below is an <em>additional</em> way in and
          is optional.
        </p>
      </div>
      <div>
        <label className="label">Extra login password {passwordSet && <span className="text-success-600 font-normal">(saved — leave blank to keep)</span>}</label>
        <div className="relative">
          <input
            type={show ? "text" : "password"}
            className="input w-full pr-10"
            placeholder={passwordSet ? "••••••" : "Optional second password"}
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            autoComplete="new-password"
          />
          <button type="button" onClick={() => setShow(!show)} className="absolute right-3 top-1/2 -translate-y-1/2 text-gray-400">
            {show ? <EyeOff size={16} /> : <Eye size={16} />}
          </button>
        </div>
        <p className="text-xs text-gray-400 mt-1">
          Optional. Both this and the admin password open the same operator account.
        </p>
      </div>
      <button onClick={save} disabled={saving} className="btn-primary w-full">{saving ? "Saving..." : "Save"}</button>
    </div>
  );
}

function GatewayTab() {
  const [testing, setTesting] = useState(false);
  const [result, setResult] = useState<any>(null);
  const [sim, setSim] = useState(1);
  const [savingSim, setSavingSim] = useState(false);
  const [gw, setGw] = useState<any>(null);

  // Multi-phone state
  const [phones, setPhones] = useState<any[]>([]);
  const [routingMode, setRoutingMode] = useState<string>("render_first");
  const [simFailover, setSimFailover] = useState<boolean>(true);
  const [savingRouting, setSavingRouting] = useState(false);
  const [showAddModal, setShowAddModal] = useState(false);
  const [editingPhone, setEditingPhone] = useState<any | null>(null);
  const [phoneBusyId, setPhoneBusyId] = useState<string | null>(null);
  const [testSmsTarget, setTestSmsTarget] = useState<Record<string, string>>({});
  const [showPass, setShowPass] = useState(false);

  const emptyForm = {
    name: "",
    mode: "cloud",
    phone_number: "",
    device_id: "",
    base_url: "https://api.sms-gate.app/3rdparty/v1",
    username: "",
    password: "",
    webhook_secret: "",
    sim_number: 1,
    priority: 10,
    daily_limit: 0,
    is_default: false,
    is_enabled: true,
    notes: "",
  };
  const [form, setForm] = useState(emptyForm);
  const [savingPhone, setSavingPhone] = useState(false);

  const loadGateway = async () => {
    try {
      const { data } = await api.get("/settings/gateway");
      setGw(data);
      if (data?.sim_number) setSim(data.sim_number);
      if (Array.isArray(data?.phones)) setPhones(data.phones);
      if (data?.routing_mode) setRoutingMode(data.routing_mode);
      if (typeof data?.sim_failover === "boolean") setSimFailover(data.sim_failover);
    } catch {}
  };

  useEffect(() => {
    loadGateway();
  }, []);

  const saveSim = async (n: number) => {
    setSim(n);
    setSavingSim(true);
    try {
      await api.put("/settings/gateway/sim", null, { params: { sim: n } });
      toast.success("SIM " + n + " selected");
    } catch {
      toast.error("Failed to set SIM");
    } finally {
      setSavingSim(false);
    }
  };

  const test = async () => {
    setTesting(true);
    setResult(null);
    try {
      const { data } = await api.post("/settings/gateway/test", null, { params: { sim } });
      setResult(data);
      toast.success(data.success ? "Connected!" : "Failed: " + (data.message || ""));
    } catch (e: any) {
      setResult({ success: false, message: e.message });
      toast.error("Test failed");
    } finally {
      setTesting(false);
    }
  };

  const saveRouting = async (nextMode: string, nextFailover: boolean) => {
    setRoutingMode(nextMode);
    setSimFailover(nextFailover);
    setSavingRouting(true);
    try {
      await api.put("/settings/gateway/routing", {
        routing_mode: nextMode,
        sim_failover: nextFailover,
      });
      toast.success("Multi-phone SMS routing updated");
    } catch (e: any) {
      toast.error(e?.response?.data?.detail || "Could not update routing");
    } finally {
      setSavingRouting(false);
    }
  };

  const openCreateModal = () => {
    setEditingPhone(null);
    setForm(emptyForm);
    setShowPass(false);
    setShowAddModal(true);
  };

  const openEditModal = (p: any) => {
    setEditingPhone(p);
    setForm({
      name: p.name || "",
      mode: p.mode || "cloud",
      phone_number: p.phone_number || "",
      device_id: p.device_id || "",
      base_url: p.base_url || "https://api.sms-gate.app/3rdparty/v1",
      username: p.username || "",
      password: "",
      webhook_secret: "",
      sim_number: p.sim_number || 1,
      priority: p.priority || 10,
      daily_limit: p.daily_limit || 0,
      is_default: !!p.is_default,
      is_enabled: !!p.is_enabled,
      notes: p.notes || "",
    });
    setShowPass(false);
    setShowAddModal(true);
  };

  const submitPhoneForm = async () => {
    if (!form.name.trim()) {
      toast.error("Give this phone a clear name (e.g. Sales Android #2)");
      return;
    }
    if (!form.username.trim()) {
      toast.error("Enter the SMS-Gate username from the phone app");
      return;
    }
    if (!editingPhone && !form.password.trim()) {
      toast.error("Enter the SMS-Gate password from the phone app");
      return;
    }
    setSavingPhone(true);
    try {
      if (editingPhone) {
        const payload: any = { ...form };
        if (!payload.password) delete payload.password;
        if (!payload.webhook_secret) delete payload.webhook_secret;
        const { data } = await api.put(`/settings/gateway/phones/${editingPhone.id}`, payload);
        if (Array.isArray(data?.phones)) setPhones(data.phones);
        else await loadGateway();
        toast.success(`Updated "${form.name}"`);
      } else {
        const { data } = await api.post("/settings/gateway/phones", form);
        if (Array.isArray(data?.phones)) setPhones(data.phones);
        else await loadGateway();
        toast.success(`Connected "${form.name}" alongside existing senders`);
      }
      setShowAddModal(false);
    } catch (e: any) {
      toast.error(e?.response?.data?.detail || "Could not save phone");
    } finally {
      setSavingPhone(false);
    }
  };

  const togglePhoneEnabled = async (p: any) => {
    setPhoneBusyId(p.id);
    try {
      const { data } = await api.put(`/settings/gateway/phones/${p.id}`, {
        is_enabled: !p.is_enabled,
      });
      if (Array.isArray(data?.phones)) setPhones(data.phones);
      toast.success(!p.is_enabled ? `${p.name} enabled` : `${p.name} paused`);
    } catch (e: any) {
      toast.error(e?.response?.data?.detail || "Could not update phone");
    } finally {
      setPhoneBusyId(null);
    }
  };

  const makeDefaultPhone = async (p: any) => {
    setPhoneBusyId(p.id);
    try {
      const { data } = await api.put(`/settings/gateway/phones/${p.id}`, { is_default: true });
      if (Array.isArray(data?.phones)) setPhones(data.phones);
      toast.success(`${p.name} set as preferred sender`);
    } catch (e: any) {
      toast.error(e?.response?.data?.detail || "Could not set preferred phone");
    } finally {
      setPhoneBusyId(null);
    }
  };

  const removePhone = async (p: any) => {
    setPhoneBusyId(p.id);
    try {
      const { data } = await api.delete(`/settings/gateway/phones/${p.id}`);
      if (Array.isArray(data?.phones)) setPhones(data.phones);
      toast.success(`Removed "${p.name}" (Render primary sender unaffected)`);
    } catch (e: any) {
      toast.error(e?.response?.data?.detail || "Could not remove phone");
    } finally {
      setPhoneBusyId(null);
    }
  };

  const testIndividualPhone = async (p: any, sendTestSms = false) => {
    const recipient = (testSmsTarget[p.id] || "").trim();
    if (sendTestSms && !recipient) {
      toast.error("Enter a recipient phone number first to send a live test SMS");
      return;
    }
    setPhoneBusyId(p.id);
    try {
      const { data } = await api.post(
        `/settings/gateway/phones/${p.id}/test`,
        sendTestSms ? { test_recipient: recipient } : {}
      );
      if (data.success) toast.success(data.message || `${p.name} online`);
      else toast.error(data.message || `${p.name} test failed`);
      await loadGateway();
    } catch (e: any) {
      toast.error(e?.response?.data?.detail || "Connection check failed");
    } finally {
      setPhoneBusyId(null);
    }
  };

  const registerIndividualWebhook = async (p: any) => {
    setPhoneBusyId(p.id);
    try {
      const { data } = await api.post(`/settings/gateway/phones/${p.id}/register-webhook`);
      toast.success(
        data.created?.length
          ? `Registered ${data.created.length} webhook event(s) on ${p.name}`
          : `${p.name} webhooks are up to date`
      );
    } catch (e: any) {
      toast.error(e?.response?.data?.detail || "Webhook registration failed");
    } finally {
      setPhoneBusyId(null);
    }
  };

  return (
    <div className="space-y-5 max-w-5xl">
      {/* Non-destructive safety banner */}
      <div className="rounded-2xl border border-primary-200 dark:border-primary-800/60 bg-primary-50/70 dark:bg-primary-950/40 px-4 py-3 text-xs text-primary-900 dark:text-primary-200 flex flex-col sm:flex-row sm:items-center justify-between gap-3">
        <div>
          <p className="font-semibold flex items-center gap-1.5">
            <ShieldCheck size={15} /> Multi-Phone SMS Sender Pool — Render Primary Protected
          </p>
          <p className="mt-0.5 text-[11.5px] opacity-90">
            Your existing Render SMS sender stays active and untouched. Connect extra Android
            SMS-Gate phones below for failover, round-robin load sharing, or dedicated SIM lines.
          </p>
        </div>
        <button type="button" onClick={openCreateModal} className="btn-primary btn-sm shrink-0">
          <Plus size={14} /> Connect Phone
        </button>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4 items-start">
        {/* Primary Render Gateway Card (preserves all existing controls & tests) */}
        <div className="card p-6 space-y-4">
          <div className="flex items-center justify-between gap-2">
            <h2 className="text-lg font-semibold flex items-center gap-2">
              <Smartphone size={18} /> Primary SMS Gateway
            </h2>
            {gw?.render_sender_active !== false && gw?.configured && (
              <span className="badge-green">Render Primary · Active</span>
            )}
          </div>
          <p className="text-xs text-gray-500">
            Connected to{" "}
            <code className="text-xs bg-gray-100 dark:bg-gray-700 px-1 rounded">
              {gw?.base_url || "api.sms-gate.app/3rdparty/v1"}
            </code>
            {gw?.username ? (
              <>
                {" "}
                Username: <strong>{gw.username}</strong>.
              </>
            ) : null}
          </p>
          {gw && !gw.configured && (
            <div className="p-3 rounded-lg text-sm bg-danger-50 dark:bg-danger-900/30 text-danger-700">
              Gateway credentials are not configured. Set SMSGATE_USERNAME and SMSGATE_PASSWORD, or
              click <strong>Connect Phone</strong> to add a phone directly here.
            </div>
          )}

          <div>
            <label className="label">SIM Slot for Sending</label>
            <div className="flex gap-2">
              <button
                onClick={() => saveSim(1)}
                className={`btn flex-1 ${sim === 1 ? "btn-primary" : "btn-secondary"}`}
                disabled={savingSim}
              >
                📱 SIM 1
              </button>
              <button
                onClick={() => saveSim(2)}
                className={`btn flex-1 ${sim === 2 ? "btn-primary" : "btn-secondary"}`}
                disabled={savingSim}
              >
                📱 SIM 2
              </button>
            </div>
            <p className="text-xs text-gray-400 mt-1">
              Select which SIM card to use on the primary sender
            </p>
          </div>

          {result && (
            <div
              className={`p-3 rounded-lg text-sm flex items-center gap-2 ${
                result.success
                  ? "bg-success-50 dark:bg-success-900/30 text-success-700"
                  : "bg-danger-50 dark:bg-danger-900/30 text-danger-700"
              }`}
            >
              {result.success ? <CheckCircle size={16} /> : <XCircle size={16} />}
              <span>{result.message || (result.success ? "Connected" : "Failed")}</span>
            </div>
          )}

          <button onClick={test} disabled={testing} className="btn-primary w-full">
            <Activity size={14} className="mr-1" />
            {testing ? "Testing..." : "Test Connection"}
          </button>

          {result && (
            <details className="text-xs bg-gray-50 dark:bg-gray-700 rounded p-2">
              <summary className="cursor-pointer font-medium">Details</summary>
              <pre className="mt-1 whitespace-pre-wrap overflow-x-auto">
                {JSON.stringify(result, null, 2)}
              </pre>
            </details>
          )}
        </div>

        {/* Multi-Phone Routing Policy Card */}
        <div className="card p-6 space-y-4">
          <div className="flex items-center justify-between">
            <h2 className="text-lg font-semibold">Sender Routing &amp; Failover</h2>
            <span className="badge-blue">{phones.length} Sender(s)</span>
          </div>
          <p className="text-xs text-gray-500">
            Control how outbound campaigns, direct SMS, and replies distribute across your Render
            primary phone and additional connected phones.
          </p>

          <div>
            <label className="label" htmlFor="sms-routing-mode">
              Outbound Routing Strategy
            </label>
            <select
              id="sms-routing-mode"
              className="input w-full"
              value={routingMode}
              disabled={savingRouting}
              onChange={(e) => saveRouting(e.target.value, simFailover)}
            >
              <option value="render_first">
                Render Primary First → Connected Phones on Failover (Recommended)
              </option>
              <option value="default_phone">
                Preferred Connected Phone First → Render Primary Backup
              </option>
              <option value="round_robin">
                Round-Robin Load Distribution Across All Active Phones
              </option>
              <option value="failover">Strict Priority Order with Automatic Failover</option>
            </select>
          </div>

          <label className="flex items-start gap-2.5 text-xs cursor-pointer pt-1">
            <input
              type="checkbox"
              checked={simFailover}
              disabled={savingRouting}
              onChange={(e) => saveRouting(routingMode, e.target.checked)}
              className="mt-0.5"
            />
            <span>
              <strong className="block text-gray-900 dark:text-gray-100">
                Automatic Dual-SIM Failover (SIM 1 ↔ SIM 2)
              </strong>
              <span className="text-gray-500">
                If the selected SIM slot fails or runs out of airtime, retry once on the second SIM
                before failing over to the next phone.
              </span>
            </span>
          </label>
        </div>
      </div>

      {/* Connected Sender Phones Roster */}
      <div className="card p-6 space-y-4">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div>
            <h2 className="text-base font-semibold">Connected SMS Sender Phones</h2>
            <p className="text-xs text-gray-500">
              Each phone uses its own encrypted SMS-Gate credentials, SIM slot, and webhook secret.
            </p>
          </div>
          <button type="button" onClick={openCreateModal} className="btn-secondary btn-sm text-xs">
            <Plus size={13} /> Add Another Phone
          </button>
        </div>

        {phones.length === 0 ? (
          <div className="rounded-xl border border-dashed border-gray-200 dark:border-gray-700 p-6 text-center text-xs text-gray-500">
            No sender phones connected yet. Click <strong>Connect Phone</strong> to add an Android
            phone running SMS-Gate.app.
          </div>
        ) : (
          <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
            {phones.map((p) => {
              const busy = phoneBusyId === p.id;
              return (
                <div
                  key={p.id}
                  className="rounded-2xl border border-gray-200 dark:border-gray-700 p-4 space-y-3 bg-white/60 dark:bg-gray-900/30"
                >
                  <div className="flex items-start justify-between gap-2">
                    <div>
                      <div className="flex flex-wrap items-center gap-1.5">
                        <span className="font-semibold text-sm">{p.name}</span>
                        {p.is_render_env && (
                          <span className="badge-green text-[10px]">Render Env · Protected</span>
                        )}
                        {p.is_default && <span className="badge-blue text-[10px]">Preferred</span>}
                        {!p.is_enabled && <span className="badge-gray text-[10px]">Paused</span>}
                      </div>
                      <p className="text-[11px] text-gray-500 mt-0.5">
                        {p.phone_number ? `${p.phone_number} · ` : ""}
                        User: <code>{p.username || "—"}</code> · SIM {p.sim_number || 1} · Mode:{" "}
                        <span className="capitalize">{p.mode || "cloud"}</span>
                      </p>
                    </div>
                    <span
                      className={`text-[10.5px] font-medium px-2 py-0.5 rounded-full ${
                        p.connection_status === "online"
                          ? "bg-success-50 text-success-700 dark:bg-success-900/30 dark:text-success-300"
                          : "bg-gray-100 text-gray-600 dark:bg-gray-800 dark:text-gray-400"
                      }`}
                    >
                      {p.connection_status || "ready"}
                    </span>
                  </div>

                  <div className="text-[11px] text-gray-500 space-y-0.5">
                    <div className="truncate">
                      Endpoint: <code>{p.base_url}</code>
                    </div>
                    <div>
                      Password: <code>{p.password_masked || (p.has_password ? "••••••" : "unset")}</code>
                      {" · "}Webhook key: {p.has_webhook_secret ? "Saved" : "Uses primary/optional"}
                    </div>
                  </div>

                  {/* Quick live test SMS row */}
                  <div className="flex gap-1.5">
                    <input
                      type="tel"
                      className="input text-xs py-1 flex-1"
                      placeholder="Test recipient e.g. +2348012345678"
                      value={testSmsTarget[p.id] || ""}
                      onChange={(e) =>
                        setTestSmsTarget((prev) => ({ ...prev, [p.id]: e.target.value }))
                      }
                    />
                    <button
                      type="button"
                      disabled={busy}
                      onClick={() => testIndividualPhone(p, true)}
                      className="btn-secondary btn-sm text-xs shrink-0"
                    >
                      <TestTube size={12} /> Send Test
                    </button>
                  </div>

                  <div className="flex flex-wrap items-center gap-1.5 pt-1 border-t border-gray-100 dark:border-gray-800">
                    <button
                      type="button"
                      disabled={busy}
                      onClick={() => testIndividualPhone(p, false)}
                      className="btn-secondary btn-sm text-[11px]"
                    >
                      Ping
                    </button>
                    <button
                      type="button"
                      disabled={busy}
                      onClick={() => registerIndividualWebhook(p)}
                      className="btn-secondary btn-sm text-[11px]"
                    >
                      Sync Webhooks
                    </button>
                    {!p.is_render_env && (
                      <>
                        <button
                          type="button"
                          disabled={busy}
                          onClick={() => openEditModal(p)}
                          className="btn-secondary btn-sm text-[11px]"
                        >
                          Edit
                        </button>
                        {!p.is_default && (
                          <button
                            type="button"
                            disabled={busy}
                            onClick={() => makeDefaultPhone(p)}
                            className="btn-secondary btn-sm text-[11px]"
                          >
                            Set Preferred
                          </button>
                        )}
                        <button
                          type="button"
                          disabled={busy}
                          onClick={() => togglePhoneEnabled(p)}
                          className="btn-secondary btn-sm text-[11px]"
                        >
                          {p.is_enabled ? "Pause" : "Resume"}
                        </button>
                        <button
                          type="button"
                          disabled={busy}
                          onClick={() => removePhone(p)}
                          className="btn-secondary btn-sm text-[11px] text-danger-600"
                        >
                          <Trash2 size={12} />
                        </button>
                      </>
                    )}
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </div>

      {/* Connect / Edit Phone Modal */}
      {showAddModal && (
        <div
          className="fixed inset-0 z-50 bg-black/50 backdrop-blur-xs flex items-center justify-center p-4"
          role="dialog"
          aria-modal="true"
          aria-label={editingPhone ? "Edit SMS Sender Phone" : "Connect SMS Sender Phone"}
        >
          <div className="card w-full max-w-lg max-h-[90vh] overflow-y-auto p-6 space-y-4">
            <div className="flex items-center justify-between">
              <h3 className="text-base font-semibold">
                {editingPhone ? `Edit ${editingPhone.name}` : "Connect New SMS Sender Phone"}
              </h3>
              <button
                type="button"
                onClick={() => setShowAddModal(false)}
                className="text-xs text-gray-500 hover:text-gray-800"
              >
                Close
              </button>
            </div>
            <p className="text-xs text-gray-500">
              Install <strong>SMS-Gate.app</strong> on your Android phone, enable Cloud or Private
              Server mode, and paste the credentials shown on the phone screen. Your existing Render
              sender remains untouched.
            </p>

            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
              <div>
                <label className="label">Phone Label *</label>
                <input
                  className="input w-full"
                  placeholder="e.g. Abuja Ops Line #2"
                  value={form.name}
                  onChange={(e) => setForm({ ...form, name: e.target.value })}
                />
              </div>
              <div>
                <label className="label">SIM Number (Optional)</label>
                <input
                  className="input w-full"
                  placeholder="+2348030000000"
                  value={form.phone_number}
                  onChange={(e) => setForm({ ...form, phone_number: e.target.value })}
                />
              </div>
            </div>

            <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
              <div>
                <label className="label">Connection Mode</label>
                <select
                  className="input w-full"
                  value={form.mode}
                  onChange={(e) => setForm({ ...form, mode: e.target.value })}
                >
                  <option value="cloud">Cloud Server</option>
                  <option value="private">Private Server</option>
                  <option value="local">Local / Tunnel</option>
                </select>
              </div>
              <div>
                <label className="label">SIM Slot</label>
                <select
                  className="input w-full"
                  value={form.sim_number}
                  onChange={(e) => setForm({ ...form, sim_number: Number(e.target.value) })}
                >
                  <option value={1}>SIM 1</option>
                  <option value={2}>SIM 2</option>
                </select>
              </div>
              <div>
                <label className="label">Priority</label>
                <input
                  type="number"
                  min={1}
                  className="input w-full"
                  value={form.priority}
                  onChange={(e) => setForm({ ...form, priority: Number(e.target.value) || 10 })}
                />
              </div>
            </div>

            <div>
              <label className="label">Gateway API Base URL</label>
              <input
                className="input w-full"
                value={form.base_url}
                onChange={(e) => setForm({ ...form, base_url: e.target.value })}
                placeholder="https://api.sms-gate.app/3rdparty/v1"
              />
            </div>

            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
              <div>
                <label className="label">SMS-Gate Username *</label>
                <input
                  className="input w-full"
                  placeholder="From SMS-Gate app"
                  value={form.username}
                  onChange={(e) => setForm({ ...form, username: e.target.value })}
                  autoComplete="off"
                />
              </div>
              <div>
                <label className="label">
                  SMS-Gate Password {editingPhone ? "(leave blank to keep)" : "*"}
                </label>
                <div className="relative">
                  <input
                    type={showPass ? "text" : "password"}
                    className="input w-full pr-9"
                    placeholder={editingPhone ? "••••••••" : "Password from app"}
                    value={form.password}
                    onChange={(e) => setForm({ ...form, password: e.target.value })}
                    autoComplete="new-password"
                  />
                  <button
                    type="button"
                    onClick={() => setShowPass((v) => !v)}
                    className="absolute right-2.5 top-1/2 -translate-y-1/2 text-gray-400"
                  >
                    {showPass ? <EyeOff size={15} /> : <Eye size={15} />}
                  </button>
                </div>
              </div>
            </div>

            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
              <div>
                <label className="label">Webhook Signing Key (Optional)</label>
                <input
                  type="password"
                  className="input w-full"
                  placeholder="Settings → Webhooks → Signing Key"
                  value={form.webhook_secret}
                  onChange={(e) => setForm({ ...form, webhook_secret: e.target.value })}
                  autoComplete="new-password"
                />
              </div>
              <div>
                <label className="label">Device ID (Optional)</label>
                <input
                  className="input w-full"
                  placeholder="Optional multi-device pin"
                  value={form.device_id}
                  onChange={(e) => setForm({ ...form, device_id: e.target.value })}
                />
              </div>
            </div>

            <label className="flex items-center gap-2 text-xs cursor-pointer">
              <input
                type="checkbox"
                checked={form.is_default}
                onChange={(e) => setForm({ ...form, is_default: e.target.checked })}
              />
              <span>Mark as preferred phone when routing mode is &ldquo;Preferred Phone First&rdquo;</span>
            </label>

            <div className="flex justify-end gap-2 pt-2">
              <button
                type="button"
                onClick={() => setShowAddModal(false)}
                className="btn-secondary"
              >
                Cancel
              </button>
              <button
                type="button"
                onClick={submitPhoneForm}
                disabled={savingPhone}
                className="btn-primary"
              >
                {savingPhone
                  ? "Saving…"
                  : editingPhone
                    ? "Save Changes"
                    : "Save & Connect Sender Phone"}
              </button>
            </div>
          </div>
        </div>
      )}

      <WebhooksCard />
    </div>
  );
}

/** Manage the webhook registrations that make inbound SMS reach the Inbox. */
function WebhooksCard() {
  const [data, setData] = useState<any>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [customUrl, setCustomUrl] = useState("");
  const [showCustom, setShowCustom] = useState(false);

  const load = async () => {
    setLoading(true);
    try { const { data } = await api.get("/settings/gateway/webhooks"); setData(data); }
    catch (e: any) { toast.error(e.response?.data?.detail || "Could not read webhooks"); }
    finally { setLoading(false); }
  };
  useEffect(() => { load(); }, []);

  const register = async () => {
    setBusy(true);
    try {
      const params = showCustom && customUrl.trim() ? { url: customUrl.trim() } : undefined;
      const { data } = await api.post("/settings/gateway/register-webhook", null, { params });
      const n = (data.created?.length ?? 0);
      toast.success(n ? `Registered ${n} event${n===1?"":"s"}` : "Already up to date");
      if (data.errors?.length) toast.error(data.errors[0]);
      await load();
    } catch (e: any) { toast.error(e.response?.data?.detail || "Registration failed"); }
    finally { setBusy(false); }
  };

  const remove = async (id: string) => {
    setBusy(true);
    try { await api.delete(`/settings/gateway/webhooks/${id}`); toast.success("Webhook removed"); await load(); }
    catch (e: any) { toast.error(e.response?.data?.detail || "Delete failed"); }
    finally { setBusy(false); }
  };

  return (
    <div className="card p-6 space-y-4 max-w-lg">
      <div className="flex items-center justify-between">
        <h2 className="text-lg font-semibold flex items-center gap-2"><Webhook size={18}/>Webhooks</h2>
        <button onClick={load} disabled={loading||busy} className="btn-secondary text-xs">
          <RefreshCw size={12} className="mr-1"/>Refresh
        </button>
      </div>
      <p className="text-xs text-gray-500">
        Incoming SMS only reach the Inbox if the gateway is told where to deliver them.
        Register once here and replies arrive automatically.
      </p>

      {loading ? <div className="skeleton h-20 w-full"/> : !data ? null : (<>
        {!data.configured && (
          <div className="p-3 rounded-lg text-sm bg-danger-50 dark:bg-danger-900/30 text-danger-700">
            Gateway credentials are missing, so webhooks cannot be registered.
          </div>
        )}
        {data.configured && !data.public_base_url_set && (
          <div className="p-3 rounded-lg text-sm bg-warning-50 dark:bg-warning-900/30 text-warning-700">
            PUBLIC_BASE_URL is not set, so we do not know this server&apos;s public address.
            Set it, or enter a URL manually below.
          </div>
        )}
        {data.configured && data.public_base_url_set && !data.signing_secret_set && (
          <div className="p-3 rounded-lg text-sm bg-warning-50 dark:bg-warning-900/30 text-warning-700">
            No signing secret set. Copy the key from the phone app (Settings &rarr; Webhooks &rarr;
            Signing Key) into SMSGATE_WEBHOOK_SECRET so deliveries can be verified.
          </div>
        )}

        {data.webhook_url && (
          <div className="text-xs">
            <span className="text-gray-500">Delivering to</span>
            <code className="block mt-0.5 break-all bg-gray-100 dark:bg-gray-700 px-2 py-1 rounded">{data.webhook_url}</code>
          </div>
        )}

        <div className={`p-3 rounded-lg text-sm flex items-start gap-2 ${data.healthy ? "bg-success-50 dark:bg-success-900/30 text-success-700" : "bg-warning-50 dark:bg-warning-900/30 text-warning-700"}`}>
          {data.healthy ? <CheckCircle size={16} className="mt-0.5 shrink-0"/> : <XCircle size={16} className="mt-0.5 shrink-0"/>}
          <span>
            {data.healthy
              ? `All ${data.registered_events?.length ?? 0} events registered — inbound SMS will reach the Inbox.`
              : `${data.missing_events?.length ?? 0} of ${data.required_events?.length ?? 0} events are not registered. Inbound SMS will NOT appear until you register them.`}
          </span>
        </div>

        {data.required_events?.length > 0 && (
          <div className="space-y-1">
            {data.required_events.map((ev: string) => {
              const on = data.registered_events?.includes(ev);
              return (
                <div key={ev} className="flex items-center justify-between text-xs py-1 border-b border-gray-100 dark:border-gray-700 last:border-0">
                  <code>{ev}</code>
                  <span className={on ? "text-success-600 font-medium" : "text-gray-400"}>
                    {on ? "registered" : "missing"}
                  </span>
                </div>
              );
            })}
          </div>
        )}

        {showCustom && (
          <div>
            <label className="label">Webhook URL</label>
            <input className="input w-full" placeholder="https://your-tunnel.ngrok.io" value={customUrl}
                   onChange={e=>setCustomUrl(e.target.value)} />
            <p className="text-xs text-gray-400 mt-1">
              Must be HTTPS. The path is appended automatically.
            </p>
          </div>
        )}

        <div className="flex gap-2">
          <button onClick={register} disabled={busy || !data.configured} className="btn-primary flex-1">
            {busy ? "Working..." : data.healthy ? "Re-register" : "Register webhooks"}
          </button>
          <button onClick={()=>setShowCustom(v=>!v)} className="btn-secondary text-xs">
            {showCustom ? "Use default" : "Custom URL"}
          </button>
        </div>

        {data.webhooks?.length > 0 && (
          <details className="text-xs">
            <summary className="cursor-pointer font-medium">
              All registrations on this gateway ({data.webhooks.length})
            </summary>
            <div className="mt-2 space-y-1">
              {data.webhooks.map((w: any) => (
                <div key={w.id} className="flex items-center justify-between gap-2 py-1 border-b border-gray-100 dark:border-gray-700 last:border-0">
                  <div className="min-w-0">
                    <code className="block truncate">{w.event}</code>
                    <span className={`block truncate text-[10px] ${w.is_ours ? "text-success-600" : "text-gray-400"}`}>
                      {w.is_ours ? "this server" : w.url}
                    </span>
                  </div>
                  <button onClick={()=>remove(w.id)} disabled={busy}
                          className="text-danger-600 hover:text-danger-700 shrink-0" title="Remove">
                    <Trash2 size={14}/>
                  </button>
                </div>
              ))}
            </div>
          </details>
        )}

        {data.error && <p className="text-xs text-danger-600 break-all">{data.error}</p>}
      </>)}
    </div>
  );
}

function NotifsTab({ provs, onUpdate }: { provs: NotificationProviderSettings[]; onUpdate: () => void }) {
  const po = provs.find(p => p.provider === "pushover");
  return <PushoverCard s={po} onUpdate={onUpdate} />;
}

function PushoverCard({ s, onUpdate }: { s?: NotificationProviderSettings; onUpdate: () => void }) {
  const [on,setOn]=useState(s?.is_enabled||false); const [uk,setUk]=useState(""); const [at,setAt]=useState(""); const [sv,setSv]=useState(false); const [ts,setTs]=useState(false); const [sa,setSa]=useState(false);
  const [muted,setMuted]=useState(""); const [mutedLoaded,setMutedLoaded]=useState(false); const [msv,setMsv]=useState(false);
  useEffect(()=>{ if(!mutedLoaded){ api.get("/settings/notifications/muted-senders").then(({data}:any)=>{ setMuted((data.senders||[]).join(", ")); setMutedLoaded(true); }).catch(()=>setMutedLoaded(true)); } },[mutedLoaded]);
  const save=async()=>{setSv(true);try{await api.put("/settings/notifications/pushover",null,{params:{is_enabled:on,user_key:uk||undefined,app_token:at||undefined}});toast.success("Saved");onUpdate();}catch{toast.error("Failed")}finally{setSv(false)}};
  const saveMuted=async()=>{setMsv(true);try{const{data}=await api.put("/settings/notifications/muted-senders",null,{params:{senders:muted}});setMuted((data.senders||[]).join(", "));toast.success("Muted senders saved");}catch{toast.error("Failed")}finally{setMsv(false)}};
  const test=async()=>{if(!uk||!at){toast.error("Enter both keys");return;}setTs(true);try{await save();const{data}=await api.post("/settings/notifications/pushover/test");data.success?toast.success("Test sent!"):toast.error(data.error||"Failed");}catch{toast.error("Failed")}finally{setTs(false)}};
  return(<div className="card p-4"><div className="flex items-center justify-between mb-3"><div className="flex items-center gap-3"><input type="checkbox" checked={on} onChange={e=>setOn(e.target.checked)}/><h3 className="font-semibold">Pushover</h3></div><button onClick={save} disabled={sv} className="btn-primary btn-sm">{sv?"Saving...":"Save"}</button></div>
  {on&&(<div className="space-y-3"><div><label className="label">User Key</label><input className="input" value={uk} onChange={e=>setUk(e.target.value)} placeholder="Pushover User Key"/></div>
  <div><label className="label">App Token</label><div className="relative"><input type={sa?"text":"password"} className="input pr-10" value={at} onChange={e=>setAt(e.target.value)} placeholder="From pushover.net/apps/build"/><button onClick={()=>setSa(!sa)} className="absolute right-3 top-1/2 -translate-y-1/2 text-gray-400">{sa?<EyeOff size={16}/>:<Eye size={16}/>}</button></div></div>
  <div className="border-t border-gray-100 dark:border-gray-700 pt-3">
    <label className="label">Muted senders — no alert (comma-separated)</label>
    <div className="flex gap-2">
      <input className="input flex-1" value={muted} onChange={e=>setMuted(e.target.value)} placeholder="MTN, AIRTEL"/>
      <button onClick={saveMuted} disabled={msv||!mutedLoaded} className="btn-secondary btn-sm">{msv?"Saving...":"Save"}</button>
    </div>
    <p className="text-xs text-gray-400 mt-1">Carrier alerts (MTN, Airtel, banks, 2FA short codes) never ping your phone. Their messages still appear in the Inbox.</p>
  </div>
  <button onClick={test} disabled={ts} className="btn-secondary btn-sm"><TestTube size={14} className="mr-1"/>{ts?"Sending...":"Send Test"}</button></div>)}</div>);}

function CompTab({ s, onUpdate }: { s: Record<string, string>; onUpdate: () => void }) {
  const [f,setF]=useState({ec:s.enable_consent_requirement==="true",ea:s.enable_auto_opt_out==="true",es:s.enable_suppression_list==="true",ek:s.enable_campaign_suppression==="true"});
  const [sv,setSv]=useState(false);
  const save=async()=>{setSv(true);try{await api.put("/settings/compliance",null,{params:{enable_consent_requirement:f.ec,enable_auto_opt_out:f.ea,enable_suppression_list:f.es,enable_campaign_suppression:f.ek}});toast.success("Saved");onUpdate();}catch{toast.error("Failed")}finally{setSv(false)}};
  return(<div className="card p-6 space-y-4"><h2 className="text-lg font-semibold">Compliance</h2><div className="space-y-3">{["Require consent","Auto-detect opt-out","Suppression list","Campaign suppression"].map((l,i)=>{const k=["ec","ea","es","ek"][i];return<label key={k} className="flex items-center gap-3 text-sm"><input type="checkbox" checked={(f as any)[k]} onChange={e=>setF({...f,[k]:e.target.checked})}/>{l}</label>})}</div><button onClick={save} disabled={sv} className="btn-primary">{sv?"Saving...":"Save"}</button></div>);}

function CallsTab() {
  const [cfg, setCfg] = useState<any>(null);
  const [loading, setLoading] = useState(true);
  const [sv, setSv] = useState(false);
  const [testing, setTesting] = useState(false);
  const [result, setResult] = useState<any>(null);
  const [showPw, setShowPw] = useState(false);
  const [showSecret, setShowSecret] = useState(false);
  const [f, setF] = useState({ base_url: "", username: "", password: "", webhook_secret: "", dial_mode: "callgate" });

  const load = async () => {
    setLoading(true);
    try {
      const { data } = await api.get("/settings/callgate");
      setCfg(data);
      setF((prev) => ({ ...prev, base_url: data.base_url || "", username: data.username || "", dial_mode: data.dial_mode || "callgate" }));
    } catch { toast.error("Could not load call settings"); }
    finally { setLoading(false); }
  };
  useEffect(() => { load(); }, []);

  const save = async () => {
    setSv(true);
    try {
      const params: any = { base_url: f.base_url, username: f.username, dial_mode: f.dial_mode };
      if (f.password) params.password = f.password;
      if (f.webhook_secret) params.webhook_secret = f.webhook_secret;
      const { data } = await api.put("/settings/callgate", null, { params });
      toast.success(data.configured ? "CallGate connected!" : "Saved — still missing details");
      setF((prev) => ({ ...prev, password: "", webhook_secret: "" }));
      await load();
    } catch (e: any) { toast.error(e.response?.data?.detail || "Save failed"); }
    finally { setSv(false); }
  };

  const test = async () => {
    setTesting(true); setResult(null);
    try {
      const { data } = await api.post("/settings/callgate/test");
      setResult(data);
      data.success ? toast.success("Connected to your phone!") : toast.error(data.message || "Failed");
    } catch (e: any) { setResult({ success: false, message: e.response?.data?.detail || e.message }); toast.error("Test failed"); }
    finally { setTesting(false); }
  };

  if (loading) return (<div className="card p-6"><div className="skeleton h-8 w-48 mb-4" /><div className="space-y-3">{[...Array(3)].map((_, i) => (<div key={i} className="skeleton h-10 w-full" />))}</div></div>);

  return (
    <div className="space-y-4">
      <div className="card p-6 space-y-4 max-w-lg">
        <div className="flex items-center justify-between">
          <h2 className="text-lg font-semibold flex items-center gap-2"><Phone size={18} />Phone Calls (CallGate)</h2>
          {cfg?.configured
            ? <span className="flex items-center gap-1 text-xs font-medium text-success-700 bg-success-50 px-2 py-1 rounded-full"><CheckCircle size={12} />Connected</span>
            : <span className="flex items-center gap-1 text-xs font-medium text-warning-700 bg-warning-50 px-2 py-1 rounded-full"><XCircle size={12} />Not set up</span>}
        </div>
        <p className="text-xs text-gray-500">
          CallGate is the companion app to SMS-Gate — install it on the <strong>same phone</strong> as SMS-Gate and this app can
          place real phone calls from Contacts, Inbox and the Phone page, using your phone's SIM.
        </p>

        <div className="rounded-lg bg-gray-50 dark:bg-gray-700/50 p-3 text-xs text-gray-600 dark:text-gray-300 space-y-1.5">
          <p className="font-semibold">Setup (2 minutes, on your phone):</p>
          <ol className="list-decimal ml-4 space-y-1">
            <li>Install <strong>CallGate</strong> from GitHub (<code>call-gate-app/android-app</code> → Releases → APK) on the same phone as SMS-Gate.</li>
            <li>Open CallGate and tap <strong>Offline → Online</strong> to start its server (port 8084).</li>
            <li>In CallGate: <strong>Settings → Server</strong> — note the username/password, find the phone's IP (Wi-Fi settings).</li>
            <li>Enter the IP + credentials below, then <strong>Save</strong> and <strong>Test connection</strong>.</li>
          </ol>
          <p className="pt-1">⚠️ The server must be able to reach the phone (same Wi-Fi/LAN, VPN like Tailscale, or a tunnel app on the phone).
            If it can't, calls automatically fall back to dialling directly from whatever device opens this app.</p>
        </div>

        <div>
          <label className="label">Phone address (CallGate server)</label>
          <input className="input w-full" placeholder="192.168.1.5:8084" value={f.base_url} onChange={(e) => setF({ ...f, base_url: e.target.value })} />
          <p className="text-xs text-gray-400 mt-1">Just the IP works — <code>/api/v1</code> and <code>http://</code> are added automatically. Default port is 8084.</p>
        </div>
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
          <div>
            <label className="label">Username</label>
            <input className="input w-full" placeholder="From CallGate → Settings → Server" value={f.username} onChange={(e) => setF({ ...f, username: e.target.value })} />
          </div>
          <div>
            <label className="label">Password {cfg?.password_set && <span className="text-success-600 font-normal">(saved {cfg.password_hint})</span>}</label>
            <div className="relative">
              <input type={showPw ? "text" : "password"} className="input w-full pr-10" placeholder={cfg?.password_set ? "•••••• (leave blank to keep)" : "CallGate password"} value={f.password} onChange={(e) => setF({ ...f, password: e.target.value })} />
              <button onClick={() => setShowPw(!showPw)} className="absolute right-3 top-1/2 -translate-y-1/2 text-gray-400">{showPw ? <EyeOff size={16} /> : <Eye size={16} />}</button>
            </div>
          </div>
        </div>
        <div>
          <label className="label">How should calls be placed?</label>
          <div className="flex gap-2">
            <button onClick={() => setF({ ...f, dial_mode: "callgate" })} className={`btn flex-1 ${f.dial_mode === "callgate" ? "btn-primary" : "btn-secondary"}`}>
              📱 Via shop phone (CallGate)
            </button>
            <button onClick={() => setF({ ...f, dial_mode: "direct" })} className={`btn flex-1 ${f.dial_mode === "direct" ? "btn-primary" : "btn-secondary"}`}>
              ☎️ Direct dial (this device)
            </button>
          </div>
          <p className="text-xs text-gray-400 mt-1">Shop-phone mode places the call on your SIM from anywhere. Direct dial opens the number on the device you're holding.</p>
        </div>

        {result && (
          <div className={`p-3 rounded-lg text-sm flex items-center gap-2 ${result.success ? "bg-success-50 dark:bg-success-900/30 text-success-700" : "bg-danger-50 dark:bg-danger-900/30 text-danger-700"}`}>
            {result.success ? <CheckCircle size={16} /> : <XCircle size={16} />}
            <span>{result.message || (result.success ? "Connected" : "Failed")}</span>
          </div>
        )}

        <div className="flex gap-2">
          <button onClick={save} disabled={sv} className="btn-primary flex-1">{sv ? "Saving..." : "Save"}</button>
          <button onClick={test} disabled={testing} className="btn-secondary flex-1">
            <Activity size={14} className="mr-1" />{testing ? "Testing..." : "Test connection"}
          </button>
        </div>
      </div>
      <CallWebhooksCard />
      <div className="card p-6 space-y-3 max-w-lg">
        <h3 className="font-semibold text-sm">Call history signing key (optional)</h3>
        <p className="text-xs text-gray-500">
          To record live call status (ringing → connected → ended) in your call history, set a webhook signing key in CallGate
          (Settings → Webhooks → Signing Key) and paste it here, then register the webhook below. Without it, calls still work —
          only the live status tracking is skipped.
        </p>
        <div className="flex gap-2">
          <div className="relative flex-1">
            <input type={showSecret ? "text" : "password"} className="input w-full pr-10" placeholder={cfg?.webhook_secret_set ? "•••••• (saved — leave blank to keep)" : "Paste signing key"} value={f.webhook_secret} onChange={(e) => setF({ ...f, webhook_secret: e.target.value })} />
            <button onClick={() => setShowSecret(!showSecret)} className="absolute right-3 top-1/2 -translate-y-1/2 text-gray-400">{showSecret ? <EyeOff size={16} /> : <Eye size={16} />}</button>
          </div>
          <button onClick={save} disabled={sv} className="btn-secondary">Save</button>
        </div>
      </div>
    </div>
  );
}

function CallWebhooksCard() {
  const [data, setData] = useState<any>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [customUrl, setCustomUrl] = useState("");
  const [showCustom, setShowCustom] = useState(false);

  const load = async () => {
    setLoading(true);
    try { const { data } = await api.get("/settings/callgate/webhooks"); setData(data); }
    catch (e: any) { setData({ error: e.response?.data?.detail || "Could not read webhooks" }); }
    finally { setLoading(false); }
  };
  useEffect(() => { load(); }, []);

  const register = async () => {
    setBusy(true);
    try {
      const params = showCustom && customUrl.trim() ? { url: customUrl.trim() } : undefined;
      const { data } = await api.post("/settings/callgate/register-webhook", null, { params });
      toast.success(data.created?.length ? `Registered ${data.created.length} event(s)` : "Already up to date");
      await load();
    } catch (e: any) { toast.error(e.response?.data?.detail || "Registration failed"); }
    finally { setBusy(false); }
  };

  const remove = async (id: string) => {
    setBusy(true);
    try { await api.delete(`/settings/callgate/webhooks/${id}`); toast.success("Webhook removed"); await load(); }
    catch (e: any) { toast.error(e.response?.data?.detail || "Delete failed"); }
    finally { setBusy(false); }
  };

  return (
    <div className="card p-6 space-y-4 max-w-lg">
      <div className="flex items-center justify-between">
        <h2 className="text-lg font-semibold flex items-center gap-2"><Webhook size={18} />Call status webhooks</h2>
        <button onClick={load} disabled={loading || busy} className="btn-secondary text-xs"><RefreshCw size={12} className="mr-1" />Refresh</button>
      </div>
      <p className="text-xs text-gray-500">CallGate tells this server when calls ring, connect and end — that's what fills your call history.</p>
      {loading ? <div className="skeleton h-20 w-full" /> : data?.error ? (
        <div className="p-3 rounded-lg text-sm bg-warning-50 dark:bg-warning-900/30 text-warning-700">{data.error}</div>
      ) : (
        <>
          <div className="space-y-1">
            {(data.events || []).map((ev: string) => {
              const on = (data.webhooks || []).some((w: any) => w.event === ev);
              return (
                <div key={ev} className="flex items-center justify-between text-xs py-1 border-b border-gray-100 dark:border-gray-700 last:border-0">
                  <code>{ev}</code>
                  <span className={on ? "text-success-600 font-medium" : "text-gray-400"}>{on ? "registered" : "missing"}</span>
                </div>
              );
            })}
          </div>
          {showCustom && (
            <div>
              <label className="label">Webhook URL</label>
              <input className="input w-full" placeholder="https://your-server.com" value={customUrl} onChange={(e) => setCustomUrl(e.target.value)} />
              <p className="text-xs text-gray-400 mt-1">Must be HTTPS and reachable FROM the phone. The path is appended automatically.</p>
            </div>
          )}
          <div className="flex gap-2">
            <button onClick={register} disabled={busy} className="btn-primary flex-1">{busy ? "Working..." : "Register webhooks"}</button>
            <button onClick={() => setShowCustom((v) => !v)} className="btn-secondary text-xs">{showCustom ? "Use default" : "Custom URL"}</button>
          </div>
          {data.webhooks?.length > 0 && (
            <details className="text-xs">
              <summary className="cursor-pointer font-medium">All registrations on this phone ({data.webhooks.length})</summary>
              <div className="mt-2 space-y-1">
                {data.webhooks.map((w: any) => (
                  <div key={w.id} className="flex items-center justify-between gap-2 py-1 border-b border-gray-100 dark:border-gray-700 last:border-0">
                    <div className="min-w-0"><code className="block truncate">{w.event}</code><span className="block truncate text-[10px] text-gray-400">{w.url}</span></div>
                    <button onClick={() => remove(w.id)} disabled={busy} className="text-danger-600 hover:text-danger-700 shrink-0" title="Remove"><Trash2 size={14} /></button>
                  </div>
                ))}
              </div>
            </details>
          )}
        </>
      )}
    </div>
  );
}


/**
 * Email (Brevo) settings.
 *
 * Credentials are never entered here twice: this tab summarises the senders
 * configured in the Email Manager and links straight to the page that owns
 * them, so there is exactly one place to add or rotate a Brevo API key.
 */
function EmailTab() {
  const [accounts, setAccounts] = useState<EmailAccount[]>([]);
  const [loading, setLoading] = useState(true);
  const [testing, setTesting] = useState<number | null>(null);

  const load = async () => {
    try {
      const data = await emailApi.listAccounts();
      setAccounts(data.items);
    } catch {
      toast.error("Could not load your email senders");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { load(); }, []);

  const test = async (account: EmailAccount) => {
    setTesting(account.id);
    try {
      const result = await emailApi.testAccount(account.id);
      if (result.success) toast.success(`"${account.name}" is connected to Brevo`);
      else toast.error(result.error || "Brevo rejected this API key");
      load();
    } catch (err: any) {
      toast.error(err.response?.data?.detail || "Could not reach Brevo");
    } finally {
      setTesting(null);
    }
  };

  if (loading) {
    return <div className="card p-6"></div>;
  }

  return (
    <div className="card p-6 space-y-4 max-w-2xl">
      <div className="flex items-start justify-between gap-3">
        <div>
          <h2 className="text-lg font-semibold flex items-center gap-2">
            <Mail size={18} /> Email sending (Brevo)
          </h2>
          <p className="text-sm text-gray-500 mt-1">
            One API key per sender — add as many as you like and point each campaign at the one it
            should use. Keys are encrypted at rest and never shown again in full.
          </p>
        </div>
        <Link to="/email-manager" className="btn-primary btn-sm whitespace-nowrap">
          <Plus size={14} className="mr-1" /> Manage senders
        </Link>
      </div>

      {accounts.length === 0 ? (
        <div className="rounded-lg bg-gray-50 dark:bg-gray-800 p-4 text-sm text-gray-600 dark:text-gray-300">
          No Brevo sender yet. Create an API key in Brevo (<strong>SMTP &amp; API → API keys</strong>),
          then add it in the Email Manager with the From name and From address you want to send as.
        </div>
      ) : (
        <div className="divide-y divide-gray-100 dark:divide-gray-800">
          {accounts.map((account) => (
            <div key={account.id} className="py-3 flex items-center justify-between gap-3">
              <div className="min-w-0">
                <p className="font-medium flex items-center gap-2 truncate">
                  {account.name}
                  {account.is_default && <span className="badge-blue">default</span>}
                  {!account.is_active && <span className="badge-gray">off</span>}
                </p>
                <p className="text-xs text-gray-500 truncate">
                  {account.from_name} &lt;{account.from_email}&gt; · key {account.api_key_masked || "—"}
                </p>
              </div>
              <button
                className="btn-secondary btn-sm shrink-0"
                onClick={() => test(account)}
                disabled={testing === account.id}
              >
                {testing === account.id ? "Testing…" : "Test"}
              </button>
            </div>
          ))}
        </div>
      )}

      <div className="rounded-lg border border-gray-200 dark:border-gray-700 p-4 text-sm space-y-2">
        <h3 className="font-semibold">Webhooks (opens, clicks, replies)</h3>
        <p className="text-gray-500">
          Each sender has its own webhook URL. Paste it into Brevo under{" "}
          <strong>Transactional → Settings → Webhook</strong> (and the inbound parsing webhook) to get
          opens, clicks, bounces and inbound replies into the app. Copy it from the senders list in the
          Email Manager.
        </p>
      </div>
    </div>
  );
}


/**
 * Gmail replies — the inbox the app reads prospects' answers from.
 *
 * Two halves, because they are two different questions:
 *
 * * "How do I make Google sign-in work?" → `GoogleOAuthSetupCard`: the callback
 *   URL to copy into the console, the client ID and secret to paste back, the
 *   numbered walkthrough, and the list of Gmail inboxes already connected.
 * * "Is anything actually arriving?" → the Replies panel from the Email Manager,
 *   reused as-is: the routing diagnosis, the per-mailbox controls, and the IMAP
 *   app-password fallback for anybody who would rather not create a Google
 *   Cloud project.
 */
function GmailTab() {
  return (
    <div className="space-y-4">
      <GoogleOAuthSetupCard returnTo="/settings?tab=gmail" />
      <MailboxRepliesTab />
    </div>
  );
}


/**
 * AI (MCP): one connector per client — ChatGPT, Claude, Arena — plus tokens and
 * the call log. The cards, the per-client setup steps and the handshake test all
 * live in `components/McpConnectors`, because each client's requirements are
 * different enough that a single generic instruction set was wrong for all three.
 */
function McpTab() {
  return (
    <div className="space-y-6">
      <AIAgentKeysCard />
      <McpConnectors />
    </div>
  );
}

function AIAgentKeysCard() {
  // One component for both pages, so the Agent screen and Settings can never
  // disagree about which key is set or which model is live — and a retired model
  // is repaired in a single place. Keys stay on the server; only masked hints
  // ever come back.
  return <AgentModelSettings />;
}
