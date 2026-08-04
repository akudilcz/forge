/**
 * Full-screen login page shown when auth is required.
 */
import { useState, FormEvent } from 'react';
import { Lock } from 'lucide-react';

interface LoginPageProps {
  onSuccess: () => void;
}

export function LoginPage({ onSuccess }: LoginPageProps) {
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setError('');
    setLoading(true);

    try {
      const res = await fetch('/auth/login', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ username, password }),
      });
      if (!res.ok) {
        setError('Invalid credentials');
        return;
      }
      onSuccess();
    } catch {
      setError('Connection failed');
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="min-h-screen bg-forge-bg flex items-center justify-center">
      <div className="w-full max-w-sm">
        <div className="bg-forge-surface border border-forge-border rounded-xl p-8">
          {/* Header */}
          <div className="flex flex-col items-center gap-3 mb-8">
            <div className="w-12 h-12 rounded-full bg-forge-accent/10 flex items-center justify-center">
              <Lock size={22} className="text-forge-accent" />
            </div>
            <h1 className="text-lg font-bold font-mono text-forge-text">FORGE</h1>
            <p className="text-xs text-forge-muted">Sign in to continue</p>
          </div>

          {/* Form */}
          <form onSubmit={handleSubmit} className="space-y-4">
            <label className="flex flex-col gap-1">
              <span className="text-[10px] font-mono text-forge-muted uppercase tracking-widest">
                Username
              </span>
              <input
                type="text"
                value={username}
                onChange={e => setUsername(e.target.value)}
                autoFocus
                autoComplete="username"
                className="bg-forge-bg border border-forge-border rounded px-3 py-2 text-sm text-forge-text font-mono focus:outline-none focus:border-forge-accent/50"
              />
            </label>

            <label className="flex flex-col gap-1">
              <span className="text-[10px] font-mono text-forge-muted uppercase tracking-widest">
                Password
              </span>
              <input
                type="password"
                value={password}
                onChange={e => setPassword(e.target.value)}
                autoComplete="current-password"
                className="bg-forge-bg border border-forge-border rounded px-3 py-2 text-sm text-forge-text font-mono focus:outline-none focus:border-forge-accent/50"
              />
            </label>

            {error && (
              <p className="text-xs text-forge-error text-center">{error}</p>
            )}

            <button
              type="submit"
              disabled={loading || !username || !password}
              className="w-full py-2.5 rounded bg-forge-accent text-white text-sm font-mono font-bold hover:bg-forge-accent/90 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
            >
              {loading ? 'Signing in...' : 'Sign in'}
            </button>
          </form>
        </div>
      </div>
    </div>
  );
}
