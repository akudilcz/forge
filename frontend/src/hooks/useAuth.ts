/**
 * Auth hook — checks whether the user is authenticated.
 */
import { useState, useEffect, useCallback } from 'react';

interface AuthState {
  loading: boolean;
  authenticated: boolean;
  authRequired: boolean;
}

export function useAuth() {
  const [state, setState] = useState<AuthState>({
    loading: true,
    authenticated: false,
    authRequired: false,
  });

  const check = useCallback(async () => {
    try {
      const res = await fetch('/auth/check');
      if (!res.ok) {
        setState({ loading: false, authenticated: false, authRequired: true });
        return;
      }
      const data = await res.json();
      setState({
        loading: false,
        authenticated: data.authenticated,
        authRequired: data.auth_required,
      });
    } catch {
      // If we can't reach the server, assume no auth required (dev mode)
      setState({ loading: false, authenticated: true, authRequired: false });
    }
  }, []);

  useEffect(() => { check(); }, [check]);

  return { ...state, recheck: check };
}
