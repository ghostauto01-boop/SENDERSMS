import { useState, useEffect, createContext, useContext, ReactNode } from "react";
import api, { setSitePasswordRequired } from "../api/client";
import { User } from "../types";

interface AuthContextType {
  user: User | null;
  loading: boolean;
  login: (password: string) => Promise<boolean>;
  logout: () => Promise<void>;
  isAuthenticated: boolean;
  /** False until a password is turned on in Settings → Site access. */
  passwordRequired: boolean;
  noteAccess: (passwordRequired: boolean) => void;
}

const AuthContext = createContext<AuthContextType>({
  user: null,
  loading: true,
  login: async () => false,
  logout: async () => {},
  isAuthenticated: false,
  passwordRequired: false,
  noteAccess: () => {},
});

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);
  const [passwordRequired, setPasswordRequired] = useState(false);

  useEffect(() => {
    checkAuth();
  }, []);

  const checkAuth = async () => {
    let required = false;
    try {
      const { data } = await api.get<{ password_required: boolean }>("/auth/access");
      required = !!data.password_required;
    } catch {
      required = false;
    }
    setPasswordRequired(required);
    setSitePasswordRequired(required);
    try {
      const { data } = await api.get<User>("/auth/me");
      setUser(data);
    } catch {
      // When the site is open (no password required), /auth/me should always succeed
      // because the backend creates/returns the admin user via ensure_admin().
      // If it fails, retry once after a short delay to handle transient errors.
      if (!required) {
        try {
          await new Promise((r) => setTimeout(r, 500));
          const retry = await api.get<User>("/auth/me");
          setUser(retry.data);
        } catch {
          setUser(null);
        }
      } else {
        setUser(null);
      }
    } finally {
      setLoading(false);
    }
  };

  const noteAccess = (required: boolean) => {
    setPasswordRequired(required);
    setSitePasswordRequired(required);
    // When access mode changes, re-fetch the user to ensure we have the correct auth state
    // This is especially important when switching from password-required to open access
    checkAuth();
  };

  const login = async (password: string): Promise<boolean> => {
    try {
      const { data } = await api.post("/auth/login", { password });
      if (data.success) {
        await checkAuth();
        return true;
      }
      return false;
    } catch {
      return false;
    }
  };

  const logout = async () => {
    try {
      await api.post("/auth/logout");
    } finally {
      setUser(null);
    }
  };

  return (
    <AuthContext.Provider
      value={{ user, loading, login, logout, isAuthenticated: !!user, passwordRequired, noteAccess }}
    >
      {children}
    </AuthContext.Provider>
  );
}

export const useAuth = () => useContext(AuthContext);
