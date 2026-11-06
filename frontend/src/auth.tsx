import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from "react";
import { api, ApiError, loadKey, setKey } from "./api";
import type { Me } from "./types";

interface AuthState {
  me: Me | null;
  loading: boolean;
  signIn: (key: string) => Promise<Me>;
  signOut: () => void;
  refresh: () => Promise<void>;
}

const AuthContext = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [me, setMe] = useState<Me | null>(null);
  const [loading, setLoading] = useState(true);

  const refresh = useCallback(async () => {
    if (!loadKey()) {
      setMe(null);
      return;
    }
    try {
      setMe(await api.get<Me>("/me"));
    } catch (e) {
      if (e instanceof ApiError && e.status === 401) setKey(null);
      setMe(null);
    }
  }, []);

  useEffect(() => {
    refresh().finally(() => setLoading(false));
  }, [refresh]);

  const signIn = useCallback(async (key: string) => {
    setKey(key.trim());
    try {
      const who = await api.get<Me>("/me");
      setMe(who);
      return who;
    } catch (e) {
      setKey(null);
      throw e;
    }
  }, []);

  const signOut = useCallback(() => {
    setKey(null);
    setMe(null);
  }, []);

  return <AuthContext.Provider value={{ me, loading, signIn, signOut, refresh }}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthState {
  const state = useContext(AuthContext);
  if (!state) throw new Error("useAuth outside AuthProvider");
  return state;
}
