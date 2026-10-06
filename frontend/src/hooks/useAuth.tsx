import { useState, useEffect, createContext, useContext, ReactNode, useCallback } from "react";
import api from "../api/client";
import { User } from "../types";

/**
 * Who is signed in, and how the sign-in screen gets there.
 *
 * The app is behind one operator password again (``ADMIN_PASSWORD``, default
 * ``12345678``). ``access`` is read from the public ``/auth/access`` endpoint so
 * the login screen knows whether to draw a password field at all — a deployment
 * that deliberately unsets every password still gets the one-tap door.
 *
 * The session itself is still a cookie: nothing is kept in localStorage, and
 * logging out clears it server-side.
 */
export interface AccessInfo {
  /** True when this deployment checks a password. */
  passwordRequired: boolean;
  /** True when the extra password from Settings → Site access is also set. */
  passwordSet: boolean;
  /** admin | site | legacy | none — which password is in effect. */
  source: string;
  /** A human sentence naming the password to type (never the password). */
  hint: string;
}

interface AuthContextType {
  user: User | null;
  loading: boolean;
  access: AccessInfo;
  /** Sign in with the app password. Resolves `true`, or the error to explain. */
  login: (password: string) => Promise<true | any>;
  /** Kept for callers that just want the door: sends the remembered password. */
  loginAsAdmin: () => Promise<true | any>;
  logout: () => Promise<void>;
  isAuthenticated: boolean;
  /** @deprecated read `access.passwordRequired` — kept for older callers. */
  passwordRequired: boolean;
  noteAccess: (passwordRequired: boolean) => void;
  refreshAccess: () => Promise<void>;
}

const DEFAULT_ACCESS: AccessInfo = {
  passwordRequired: false,
  passwordSet: false,
  source: "none",
  hint: "",
};

const AuthContext = createContext<AuthContextType>({
  user: null,
  loading: true,
  access: DEFAULT_ACCESS,
  login: async () => false,
  loginAsAdmin: async () => false,
  logout: async () => {},
  isAuthenticated: false,
  passwordRequired: false,
  noteAccess: () => {},
  refreshAccess: async () => {},
});

//: The username used by the one-field login form.
const OPERATOR_USERNAME = "admin";
//: Remembers the last accepted password in this browser (sessionStorage, not
//: localStorage: it dies with the tab) so a reload does not ask twice.
const REMEMBER_KEY = "sendsms.lastPassword";

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);
  const [access, setAccess] = useState<AccessInfo>(DEFAULT_ACCESS);

  const refreshAccess = useCallback(async () => {
    try {
      const { data } = await api.get("/auth/access");
      setAccess({
        passwordRequired: !!data.password_required,
        passwordSet: !!data.password_set,
        source: data.source || "none",
        hint: data.hint || "",
      });
    } catch {
      // Offline or database down: leave the last known answer. The login form
      // still submits and reports whatever the server says.
    }
  }, []);

  const checkAuth = useCallback(async () => {
    try {
      const { data } = await api.get<User>("/auth/me");
      setUser(data);
    } catch {
      setUser(null);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    refreshAccess();
    checkAuth();
  }, [refreshAccess, checkAuth]);

  const noteAccess = (_required: boolean) => {
    // The Settings page calls this after changing the site password; re-reading
    // the endpoint is the honest way to reflect it.
    refreshAccess();
  };

  const login = useCallback(
    async (password: string): Promise<true | any> => {
      try {
        const { data } = await api.post("/auth/login", {
          username: OPERATOR_USERNAME,
          password,
        });
        if (data?.success) {
          try {
            window.sessionStorage.setItem(REMEMBER_KEY, password);
          } catch {
            /* private mode — no harm */
          }
          await checkAuth();
          refreshAccess();
          return true;
        }
        return new Error(data?.message || "Sign-in was rejected");
      } catch (err: any) {
        return err ?? new Error("Sign-in failed");
      }
    },
    [checkAuth, refreshAccess]
  );

  const loginAsAdmin = useCallback(async (): Promise<true | any> => {
    // Only used by the one-tap button a password-less deployment shows.
    let remembered = "";
    try {
      remembered = window.sessionStorage.getItem(REMEMBER_KEY) || "";
    } catch {
      /* ignore */
    }
    try {
      const { data } = await api.post("/auth/admin", remembered ? { password: remembered } : {});
      if (data?.success) {
        await checkAuth();
        return true;
      }
      return new Error(data?.message || "Sign-in was rejected");
    } catch (err: any) {
      return err ?? new Error("Sign-in failed");
    }
  }, [checkAuth]);

  const logout = async () => {
    try {
      await api.post("/auth/logout");
    } finally {
      try {
        window.sessionStorage.removeItem(REMEMBER_KEY);
      } catch {
        /* ignore */
      }
      setUser(null);
    }
  };

  return (
    <AuthContext.Provider
      value={{
        user,
        loading,
        access,
        login,
        loginAsAdmin,
        logout,
        isAuthenticated: !!user,
        passwordRequired: access.passwordRequired,
        noteAccess,
        refreshAccess,
      }}
    >
      {children}
    </AuthContext.Provider>
  );
}

export const useAuth = () => useContext(AuthContext);
