import { useState, useEffect, createContext, useContext, ReactNode } from "react";
import api from "../api/client";
import { User } from "../types";

interface AuthContextType {
  user: User | null;
  loading: boolean;
  /**
   * One-tap sign-in: no username, no password.
   * Resolves `true` on success, otherwise the error (so the caller can say
   * *why* — e.g. the database being down — instead of a generic message).
   */
  loginAsAdmin: () => Promise<true | any>;
  logout: () => Promise<void>;
  isAuthenticated: boolean;
  /** The password wall is gone — nothing ever asks for a password. */
  passwordRequired: boolean;
  noteAccess: (passwordRequired: boolean) => void;
}

const AuthContext = createContext<AuthContextType>({
  user: null,
  loading: true,
  loginAsAdmin: async () => false,
  logout: async () => {},
  isAuthenticated: false,
  passwordRequired: false,
  noteAccess: () => {},
});

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    checkAuth();
  }, []);

  const checkAuth = async () => {
    try {
      const { data } = await api.get<User>("/auth/me");
      setUser(data);
    } catch {
      setUser(null);
    } finally {
      setLoading(false);
    }
  };

  const noteAccess = (_required: boolean) => {
    // Kept for the Settings page. No gate exists any more, so there is
    // nothing to switch on or off.
  };

  const loginAsAdmin = async (): Promise<true | any> => {
    try {
      const { data } = await api.post("/auth/admin");
      if (data.success) {
        await checkAuth();
        return true;
      }
      return new Error(data.message || "Sign-in was rejected");
    } catch (err) {
      return err ?? new Error("Sign-in failed");
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
      value={{
        user,
        loading,
        loginAsAdmin,
        logout,
        isAuthenticated: !!user,
        passwordRequired: false,
        noteAccess,
      }}
    >
      {children}
    </AuthContext.Provider>
  );
}

export const useAuth = () => useContext(AuthContext);
