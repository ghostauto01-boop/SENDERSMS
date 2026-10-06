import { useEffect, useRef, useState } from "react";
import { useNavigate, Navigate } from "react-router-dom";
import { useAuth } from "../hooks/useAuth";
import toast from "react-hot-toast";
import { Eye, EyeOff, KeyRound, Loader2, LogIn, ShieldCheck } from "lucide-react";
import BrandMark from "../components/BrandMark";
import api from "../api/client";
import { dbOutageFromError, DbOutage, dbOutageTitle, subscribeDbStatus } from "../utils/dbStatus";

/**
 * The sign-in screen.
 *
 * One field: the app password (``ADMIN_PASSWORD``, default ``12345678``). It is
 * submitted to ``/auth/login``, so a wrong password is refused with the server's
 * own words instead of a generic failure. A deployment that removed every
 * password still gets the one-tap button — that is what ``access.passwordRequired``
 * decides, and it is read from the public ``/auth/access`` endpoint rather than
 * guessed on the client.
 */
export default function LoginPage() {
  const [password, setPassword] = useState("");
  const [show, setShow] = useState(false);
  const [loading, setLoading] = useState(false);
  const [outage, setOutage] = useState<DbOutage | null>(null);
  const { login, loginAsAdmin, isAuthenticated, loading: authLoading, access } = useAuth();
  const navigate = useNavigate();
  const inputRef = useRef<HTMLInputElement>(null);

  // Mirror the global banner state so the button can explain a failure.
  useEffect(() => subscribeDbStatus(setOutage), []);

  // The one password field is the only thing on this screen: focus it, and
  // check the database once so a broken backend is explained before typing.
  useEffect(() => {
    const timer = window.setTimeout(() => inputRef.current?.focus(), 250);
    api.get("/health/db").catch(() => {
      /* the interceptor already reported the outage to the banner */
    });
    return () => window.clearTimeout(timer);
  }, []);

  if (authLoading) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-canvas dark:bg-canvas-dark">
        <div className="animate-spin rounded-full h-12 w-12 border-b-2 border-primary-600" />
      </div>
    );
  }

  if (isAuthenticated) {
    return <Navigate to="/dashboard" replace />;
  }

  const explain = (result: true | any) => {
    const reason = dbOutageFromError(result);
    if (reason) {
      toast.error(`${dbOutageTitle(reason.kind)}. ${reason.message}`, { duration: 8000 });
    } else if (result?.response?.data?.detail) {
      toast.error(String(result.response.data.detail));
    } else if (result instanceof Error) {
      toast.error(result.message);
    } else {
      toast.error("Could not sign in — is the server reachable?");
    }
  };

  const handleSubmit = async (event?: React.FormEvent) => {
    event?.preventDefault();
    if (loading) return;
    setLoading(true);
    const result = await login(password);
    setLoading(false);
    if (result === true) {
      toast.success("Signed in");
      navigate("/dashboard");
      return;
    }
    explain(result);
    inputRef.current?.select();
  };

  const handleOneTap = async () => {
    setLoading(true);
    const result = await loginAsAdmin();
    setLoading(false);
    if (result === true) {
      toast.success("Signed in as admin");
      navigate("/dashboard");
      return;
    }
    explain(result);
  };

  const needsPassword = access.passwordRequired !== false;

  return (
    <div className="relative min-h-screen flex items-center justify-center overflow-hidden bg-canvas dark:bg-canvas-dark px-4 py-10">
      {/* Soft brand wash behind the card — the only decoration on the screen. */}
      <div
        aria-hidden
        className="pointer-events-none absolute -top-40 -left-32 h-96 w-96 rounded-full bg-primary-500/20 blur-3xl animate-fade-in"
      />
      <div
        aria-hidden
        className="pointer-events-none absolute -bottom-40 -right-24 h-96 w-96 rounded-full bg-accent-500/20 blur-3xl animate-fade-in"
      />

      <div className="relative w-full max-w-md animate-slide-up">
        <div className="text-center mb-7">
          <BrandMark className="w-16 h-16 mx-auto mb-4 rounded-2xl shadow-raised" />
          <h1 className="text-2xl font-bold text-gray-900 dark:text-white tracking-tight">
            SMS SENDER
          </h1>
          <p className="text-gray-500 dark:text-gray-400 mt-1">
            Nigerian SMS &amp; Email Outreach CRM
          </p>
        </div>

        <form onSubmit={handleSubmit} className="card p-6 shadow-raised">
          {needsPassword ? (
            <>
              <label htmlFor="password" className="label flex items-center gap-1.5">
                <KeyRound size={14} className="text-primary-600" /> Admin password
              </label>
              <div className="relative">
                <input
                  id="password"
                  ref={inputRef}
                  type={show ? "text" : "password"}
                  autoComplete="current-password"
                  className="input pr-11"
                  placeholder="Enter the admin password"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  disabled={loading}
                />
                <button
                  type="button"
                  onClick={() => setShow((s) => !s)}
                  className="absolute right-2 top-1/2 -translate-y-1/2 p-2 rounded-lg text-gray-400 hover:text-gray-600 dark:hover:text-gray-300 transition-colors"
                  aria-label={show ? "Hide password" : "Show password"}
                  tabIndex={-1}
                >
                  {show ? <EyeOff size={16} /> : <Eye size={16} />}
                </button>
              </div>

              <button
                type="submit"
                disabled={loading || !password}
                className="btn-primary w-full mt-4"
              >
                {loading ? (
                  <>
                    <Loader2 size={18} className="animate-spin" />
                    Signing in…
                  </>
                ) : (
                  <>
                    <LogIn size={18} />
                    Sign in
                  </>
                )}
              </button>

              <p className="text-xs text-gray-500 dark:text-gray-400 mt-3 text-center flex items-center justify-center gap-1.5">
                <ShieldCheck size={13} className="text-success-500" />
                {access.hint || "The password set for this deployment."}
              </p>
            </>
          ) : (
            <>
              <button
                type="button"
                onClick={handleOneTap}
                disabled={loading}
                className="btn-primary w-full"
              >
                {loading ? (
                  <>
                    <Loader2 size={18} className="animate-spin" />
                    Signing in…
                  </>
                ) : (
                  <>
                    <LogIn size={18} />
                    Log in as admin
                  </>
                )}
              </button>
              <p className="text-xs text-gray-400 mt-3 text-center">
                {outage
                  ? "Signing in needs the database, which is currently unavailable — see the notice above."
                  : "This deployment has no password set."}
              </p>
            </>
          )}
        </form>

        <p className="text-center text-xs text-gray-400 mt-6">
          Admin password can be changed any time by setting{" "}
          <code className="px-1 py-0.5 rounded bg-gray-100 dark:bg-gray-800 text-gray-500 dark:text-gray-400">
            ADMIN_PASSWORD
          </code>
        </p>
      </div>
    </div>
  );
}
