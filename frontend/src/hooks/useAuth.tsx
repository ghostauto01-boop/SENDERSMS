import { useState, useEffect, createContext, useContext, ReactNode } from "react";
import api from "../api/client";
import { User } from "../types";

interface AuthContextType {
  user: User | null;
  loading: boolean;
  /** One-tap sign-in: no username, no password. */
  loginAsAdmin: () => Promise<boolean>;
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

  const loginAsAdmin = async (): Promise<boolean> => {
    try {
      const { data } = await api.post("/auth/admin");
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
