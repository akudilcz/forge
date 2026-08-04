/**
 * Settings dashboard — edit settings via /api/settings.
 * Auto-saves after 600 ms of inactivity.
 */
import { useState, useEffect, useRef, useCallback } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { Settings as SettingsIcon, Save, RefreshCw, Key } from 'lucide-react';
import { useStore } from '@/store';

// ── Types ──────────────────────────────────────────────────────────────────────

type SaveStatus = 'idle' | 'saving' | 'saved' | 'error';

// ── API ───────────────────────────────────────────────────────────────────────

async function fetchSettings(): Promise<Record<string, unknown>> {
  const r = await fetch('/api/settings');
  if (!r.ok) throw new Error('Failed to load settings');
  return r.json();
}

async function patchSettings(patch: Record<string, unknown>): Promise<Record<string, unknown>> {
  const r = await fetch('/api/settings', {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(patch),
  });
  if (!r.ok) throw new Error('Failed to save settings');
  return r.json();
}

// ── Field primitives ──────────────────────────────────────────────────────────

function FieldText({ label, value, onChange, mono }: {
  label: string; value: string; onChange: (v: string) => void; mono?: boolean;
}) {
  return (
    <label className="flex flex-col gap-1">
      <span className="text-[10px] font-mono text-forge-muted uppercase tracking-widest">{label}</span>
      <input
        type="text"
        value={value}
        onChange={e => onChange(e.target.value)}
        className={`bg-forge-bg border border-forge-border rounded px-2.5 py-1.5 text-xs text-forge-text focus:outline-none focus:border-forge-accent/50 ${mono ? 'font-mono' : ''}`}
      />
    </label>
  );
}

function FieldNumber({ label, value, onChange, min, max }: {
  label: string; value: number; onChange: (v: number) => void; min?: number; max?: number;
}) {
  return (
    <label className="flex flex-col gap-1">
      <span className="text-[10px] font-mono text-forge-muted uppercase tracking-widest">{label}</span>
      <input
        type="number"
        value={value}
        min={min}
        max={max}
        onChange={e => onChange(Number(e.target.value))}
        className="bg-forge-bg border border-forge-border rounded px-2.5 py-1.5 text-xs text-forge-text font-mono focus:outline-none focus:border-forge-accent/50 w-full"
      />
    </label>
  );
}

function FieldList({ label, value, onChange, help }: {
  label: string; value: string[]; onChange: (v: string[]) => void; help?: string;
}) {
  const text = value.join('\n');
  return (
    <label className="flex flex-col gap-1">
      <span className="text-[10px] font-mono text-forge-muted uppercase tracking-widest">{label}</span>
      {help && <span className="text-[10px] text-forge-muted">{help}</span>}
      <textarea
        value={text}
        rows={4}
        onChange={e => onChange(e.target.value.split('\n').filter(Boolean))}
        className="bg-forge-bg border border-forge-border rounded px-2.5 py-1.5 text-xs text-forge-text font-mono focus:outline-none focus:border-forge-accent/50 resize-y"
      />
    </label>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="bg-forge-surface rounded-xl border border-forge-border overflow-hidden">
      <div className="px-4 py-3 border-b border-forge-border bg-forge-bg/50">
        <h2 className="text-sm font-bold font-mono text-forge-text uppercase">{title}</h2>
      </div>
      <div className="p-4 grid grid-cols-2 gap-4">
        {children}
      </div>
    </div>
  );
}

function FullSpan({ children }: { children: React.ReactNode }) {
  return <div className="col-span-2">{children}</div>;
}

// ── Sections ──────────────────────────────────────────────────────────────────

const PHASE_NAMES: Record<string, string> = {
  '1': 'Ingest', '2': 'Chunking', '3': 'HLR', '4': 'Architecture',
  '5': 'Modules', '6': 'Contracts', '7': 'LLR', '8': 'Design',
  '9': 'Test Suite', '10': 'Verification', '11': 'Doco Render', '12': 'Code Gen',
};

const PROVIDER_OPTIONS = [
  { key: 'poe', label: 'Poe' },
  { key: 'openrouter', label: 'OpenRouter' },
];

const AGENT_ROLES = [
  'Document Specialist', 'Requirements Engineer', 'Design Architect',
  'Software Engineer', 'Test Engineer', 'Quality Auditor', 'Console',
];

function LLMSection({ cfg, set }: { cfg: Record<string, unknown>; set: (k: string, v: unknown) => void }) {
  const llm = cfg.llm as Record<string, unknown> ?? {};
  const opts = llm.options as Record<string, unknown> ?? {};
  const models = llm.phase_models as Record<string, string> ?? {};
  const rawAgentModels = llm.agents as Record<string, string> ?? {};
  // Fill missing agent roles from the most common phase model
  const defaultModel = models['1'] ?? '';
  const agentModels: Record<string, string> = Object.fromEntries(
    AGENT_ROLES.map(r => [r, rawAgentModels[r] || defaultModel]),
  );
  const ctxWindows = llm.model_context_windows as Record<string, number> ?? {};
  const providers = llm.providers as Record<string, Record<string, unknown>> ?? {};
  const activeProvider = String(llm.active_provider ?? 'poe');

  const patch = (k: string, v: unknown) => set('llm', { ...llm, [k]: v });
  const patchOpt = (k: string, v: unknown) => set('llm', { ...llm, options: { ...opts, [k]: v } });
  const patchModel = (phase: string, model: string) =>
    set('llm', { ...llm, phase_models: { ...models, [phase]: model } });
  const patchAgentModel = (role: string, model: string) =>
    set('llm', { ...llm, agents: { ...agentModels, [role]: model } });
  const patchCtxWindow = (model: string, tokens: number) =>
    set('llm', { ...llm, model_context_windows: { ...ctxWindows, [model]: tokens } });

  const switchProvider = (newKey: string) => {
    if (newKey === activeProvider) return;
    // Save current config into old provider slot
    const saved: Record<string, Record<string, unknown>> = {
      ...providers,
      [activeProvider]: {
        base_url: llm.base_url,
        api_key_env: llm.api_key_env,
        agents: agentModels,
        phase_models: models,
        model_context_windows: ctxWindows,
      },
    };
    // Load new provider's saved config (or empty defaults)
    const incoming = saved[newKey] ?? {};
    set('llm', {
      ...llm,
      active_provider: newKey,
      base_url: incoming.base_url ?? '',
      api_key_env: incoming.api_key_env ?? '',
      agents: incoming.agents ?? {},
      phase_models: incoming.phase_models ?? {},
      model_context_windows: incoming.model_context_windows ?? {},
      providers: saved,
    });
  };

  // Collect unique models used across phases + agents
  const allModelValues = [...Object.values(models), ...Object.values(agentModels)];
  const uniqueModels = [...new Set(allModelValues)].filter(Boolean).sort();

  return (
    <div className="bg-forge-surface rounded-xl border border-forge-border overflow-hidden">
      <div className="px-4 py-3 border-b border-forge-border bg-forge-bg/50">
        <h2 className="text-sm font-bold font-mono text-forge-text uppercase">LLM</h2>
      </div>
      <div className="p-4 space-y-4">
        {/* Provider selector */}
        <div>
          <span className="text-[10px] font-mono text-forge-muted uppercase tracking-widest">Active Provider</span>
          <div className="mt-1.5 flex gap-2">
            {PROVIDER_OPTIONS.map(opt => (
              <button
                key={opt.key}
                onClick={() => switchProvider(opt.key)}
                className={`px-4 py-1.5 text-xs font-mono rounded border transition-colors ${
                  activeProvider === opt.key
                    ? 'bg-forge-accent text-white border-forge-accent'
                    : 'bg-forge-bg text-forge-muted border-forge-border hover:border-forge-accent/50 hover:text-forge-text'
                }`}
              >
                {opt.label}
              </button>
            ))}
          </div>
        </div>
        {/* Provider / Endpoint */}
        <div className="grid grid-cols-2 gap-4">
          <FieldText label="Base URL" value={String(llm.base_url ?? '')} onChange={v => patch('base_url', v)} mono />
          <FieldText label="API Key Env Var" value={String(llm.api_key_env ?? '')} onChange={v => patch('api_key_env', v)} mono />
        </div>
        {/* Phase model table */}
        <div>
          <span className="text-[10px] font-mono text-forge-muted uppercase tracking-widest">Model per Phase</span>
          <div className="mt-2 space-y-1.5">
            {Object.entries(PHASE_NAMES).map(([num, name]) => (
              <div key={num} className="flex items-center gap-3">
                <span className="text-[10px] font-mono text-forge-muted w-6 text-right shrink-0">{num}</span>
                <span className="text-xs font-mono text-forge-text w-28 shrink-0">{name}</span>
                <input
                  type="text"
                  value={models[num] ?? ''}
                  onChange={e => patchModel(num, e.target.value)}
                  className="flex-1 bg-forge-bg border border-forge-border rounded px-2 py-1 text-xs text-forge-text font-mono focus:outline-none focus:border-forge-accent/50"
                />
              </div>
            ))}
          </div>
        </div>
        {/* Agent model table */}
        <div className="pt-2 border-t border-forge-border">
          <span className="text-[10px] font-mono text-forge-muted uppercase tracking-widest">Model per Agent</span>
          <div className="mt-2 space-y-1.5">
            {AGENT_ROLES.map(role => (
              <div key={role} className="flex items-center gap-3">
                <span className="text-xs font-mono text-forge-text w-40 shrink-0">{role}</span>
                <input
                  type="text"
                  value={agentModels[role] ?? ''}
                  onChange={e => patchAgentModel(role, e.target.value)}
                  className="flex-1 bg-forge-bg border border-forge-border rounded px-2 py-1 text-xs text-forge-text font-mono focus:outline-none focus:border-forge-accent/50"
                />
              </div>
            ))}
          </div>
        </div>
        {/* Context windows per model */}
        {uniqueModels.length > 0 && (
          <div className="pt-2 border-t border-forge-border">
            <span className="text-[10px] font-mono text-forge-muted uppercase tracking-widest">Context Window per Model</span>
            <div className="mt-2 space-y-1.5">
              {uniqueModels.map(model => (
                <div key={model} className="flex items-center gap-3">
                  <span className="text-xs font-mono text-forge-text flex-1 min-w-0 truncate">{model}</span>
                  <div className="flex items-center gap-1">
                    <input
                      type="number"
                      value={ctxWindows[model] ?? 128000}
                      onChange={e => patchCtxWindow(model, Number(e.target.value))}
                      min={4096}
                      step={1000}
                      className="w-28 bg-forge-bg border border-forge-border rounded px-2 py-1 text-xs text-forge-text font-mono focus:outline-none focus:border-forge-accent/50 text-right"
                    />
                    <span className="text-[10px] font-mono text-forge-muted">tok</span>
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}
        {/* Tuning */}
        <div className="grid grid-cols-3 gap-4 pt-2 border-t border-forge-border">
          <FieldNumber label="Temperature" value={Number(opts.temperature ?? 0.8)} onChange={v => patchOpt('temperature', v)} />
          <FieldNumber label="Timeout (s)" value={Number(llm.request_timeout ?? 120)} onChange={v => patch('request_timeout', v)} min={10} />
          <FieldNumber label="Call Delay (ms)" value={Number(llm.call_delay_ms ?? 400)} onChange={v => patch('call_delay_ms', v)} min={0} max={5000} />
        </div>
      </div>
    </div>
  );
}

function ProjectSection({ cfg, set }: { cfg: Record<string, unknown>; set: (k: string, v: unknown) => void }) {
  const proj = cfg.project as Record<string, unknown> ?? {};
  const patch = (k: string, v: unknown) => set('project', { ...proj, [k]: v });
  return (
    <Section title="Project">
      <FullSpan><FieldText label="Name" value={String(proj.name ?? '')} onChange={v => patch('name', v)} /></FullSpan>
      <FieldText label="Forge.md" value={String(proj.forgemd ?? '')} onChange={v => patch('forgemd', v)} mono />
      <FullSpan><FieldText label="Workspace Directory" value={String(proj.workspace_dir ?? '/store/forge/workspace/')} onChange={v => patch('workspace_dir', v)} mono /></FullSpan>
    </Section>
  );
}

function ServerSection({ cfg, set }: { cfg: Record<string, unknown>; set: (k: string, v: unknown) => void }) {
  const srv = cfg.server as Record<string, unknown> ?? {};
  const patch = (k: string, v: unknown) => set('server', { ...srv, [k]: v });
  return (
    <Section title="Server">
      <FieldText label="Host" value={String(srv.host ?? 'localhost')} onChange={v => patch('host', v)} mono />
      <FieldNumber label="Port" value={Number(srv.port ?? 7340)} onChange={v => patch('port', v)} min={1024} max={65535} />
    </Section>
  );
}

function ToolsSection({ cfg, set }: { cfg: Record<string, unknown>; set: (k: string, v: unknown) => void }) {
  const tools = cfg.tools as Record<string, unknown> ?? {};
  const patch = (k: string, v: unknown) => set('tools', { ...tools, [k]: v });
  return (
    <Section title="Tools">
      <FullSpan>
        <FieldList
          label="Shell exec allowlist"
          value={(tools.shell_exec_allowlist as string[]) ?? []}
          onChange={v => patch('shell_exec_allowlist', v)}
          help="One glob pattern per line"
        />
      </FullSpan>
      <FullSpan>
        <FieldList
          label="Web fetch allowlist"
          value={(tools.web_fetch_allowlist as string[]) ?? []}
          onChange={v => patch('web_fetch_allowlist', v)}
          help="One domain per line"
        />
      </FullSpan>
    </Section>
  );
}

// ── API Keys ─────────────────────────────────────────────────────────────────

interface SecretEntry {
  name: string;
  label: string;
  is_set: boolean;
}

function ApiKeysSection() {
  const qc = useQueryClient();
  const { data: secrets } = useQuery<SecretEntry[]>({
    queryKey: ['secrets'],
    queryFn: () => fetch('/api/secrets').then(r => r.json()),
  });

  const [editing, setEditing] = useState<string | null>(null);
  const [value, setValue] = useState('');
  const [saving, setSaving] = useState(false);

  async function handleSave(name: string) {
    setSaving(true);
    await fetch('/api/secrets', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name, value }),
    });
    setSaving(false);
    setEditing(null);
    setValue('');
    qc.invalidateQueries({ queryKey: ['secrets'] });
  }

  async function handleDelete(name: string) {
    await fetch(`/api/secrets/${name}`, { method: 'DELETE' });
    qc.invalidateQueries({ queryKey: ['secrets'] });
  }

  return (
    <div className="bg-forge-surface rounded-xl border border-forge-border overflow-hidden">
      <div className="px-4 py-3 border-b border-forge-border bg-forge-bg/50 flex items-center gap-2">
        <Key size={14} className="text-forge-accent" />
        <h2 className="text-sm font-bold font-mono text-forge-text uppercase">API Keys</h2>
      </div>
      <div className="p-4 space-y-3">
        {(secrets ?? []).map(secret => (
          <div key={secret.name} className="flex items-center gap-3">
            <div className="flex-1 min-w-0">
              <p className="text-xs font-mono text-forge-text">{secret.label}</p>
              <p className="text-[10px] text-forge-muted font-mono">{secret.name}</p>
            </div>
            {editing === secret.name ? (
              <div className="flex items-center gap-2">
                <input
                  type="password"
                  value={value}
                  onChange={e => setValue(e.target.value)}
                  placeholder="Paste key..."
                  autoFocus
                  className="bg-forge-bg border border-forge-border rounded px-2 py-1 text-xs text-forge-text font-mono w-64 focus:outline-none focus:border-forge-accent/50"
                />
                <button
                  onClick={() => handleSave(secret.name)}
                  disabled={saving || !value}
                  className="px-2 py-1 text-[10px] font-mono bg-forge-accent text-white rounded hover:bg-forge-accent/90 disabled:opacity-40"
                >
                  Save
                </button>
                <button
                  onClick={() => { setEditing(null); setValue(''); }}
                  className="px-2 py-1 text-[10px] font-mono text-forge-muted hover:text-forge-text"
                >
                  Cancel
                </button>
              </div>
            ) : (
              <div className="flex items-center gap-2">
                <span className={`text-[10px] font-mono px-2 py-0.5 rounded ${
                  secret.is_set
                    ? 'bg-forge-success/10 text-forge-success'
                    : 'bg-forge-error/10 text-forge-error'
                }`}>
                  {secret.is_set ? 'configured' : 'not set'}
                </span>
                <button
                  onClick={() => { setEditing(secret.name); setValue(''); }}
                  className="px-2 py-1 text-[10px] font-mono text-forge-muted hover:text-forge-accent"
                >
                  {secret.is_set ? 'Change' : 'Set'}
                </button>
                {secret.is_set && (
                  <button
                    onClick={() => handleDelete(secret.name)}
                    className="px-2 py-1 text-[10px] font-mono text-forge-muted hover:text-forge-error"
                  >
                    Remove
                  </button>
                )}
              </div>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}

// ── Main ──────────────────────────────────────────────────────────────────────

export function Settings() {
  const qc = useQueryClient();
  const { data: remote, isLoading } = useQuery({
    queryKey: ['settings'],
    queryFn: fetchSettings,
  });

  const [local, setLocal] = useState<Record<string, unknown> | null>(null);
  const [saveStatus, setSaveStatus] = useState<SaveStatus>('idle');
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  // Initialise local state from remote
  useEffect(() => {
    if (remote && local === null) setLocal(remote);
  }, [remote, local]);

  // Reset save icon back to idle after 2 s
  useEffect(() => {
    if (saveStatus !== 'saved') return;
    const t = setTimeout(() => setSaveStatus('idle'), 2000);
    return () => clearTimeout(t);
  }, [saveStatus]);

  const mutation = useMutation({
    mutationFn: patchSettings,
    onMutate:  () => setSaveStatus('saving'),
    onSuccess: (data) => { qc.setQueryData(['settings'], data); setSaveStatus('saved'); },
    onError:   () => setSaveStatus('error'),
  });

  const logAction = useStore((s) => s.logUserAction);
  const setSection = useCallback((key: string, value: unknown) => {
    logAction(`Settings changed: ${key}`);
    setLocal(prev => {
      const next = { ...(prev ?? {}), [key]: value };
      // Debounce save
      if (debounceRef.current) clearTimeout(debounceRef.current);
      debounceRef.current = setTimeout(() => {
        mutation.mutate({ [key]: value });
      }, 600);
      return next;
    });
    setSaveStatus('idle');
  }, [mutation, logAction]);

  if (isLoading || local === null) {
    return (
      <div className="h-full flex items-center justify-center">
        <RefreshCw size={20} className="text-forge-muted animate-spin" />
      </div>
    );
  }

  return (
    <div className="h-full flex flex-col overflow-y-auto animate-fade-in">
      {/* Header */}
      <div className="flex items-center justify-between px-6 py-4 border-b border-forge-border shrink-0">
        <div className="flex items-center gap-3">
          <SettingsIcon size={20} className="text-forge-accent" />
          <div>
            <h1 className="text-xl font-bold font-mono text-forge-text">Settings</h1>
          </div>
        </div>
        <Save
          size={14}
          className={`transition-colors duration-300 ${
            saveStatus === 'saved'  ? 'text-forge-success' :
            saveStatus === 'error'  ? 'text-forge-error'   :
            saveStatus === 'saving' ? 'text-forge-accent'  :
            'text-forge-muted'
          }`}
        />
      </div>

      {/* Sections */}
      <div className="flex-1 p-6 space-y-4">
        <ApiKeysSection />
        <LLMSection           cfg={local} set={setSection} />
        <ProjectSection       cfg={local} set={setSection} />
        <ServerSection        cfg={local} set={setSection} />
        <ToolsSection         cfg={local} set={setSection} />
      </div>
    </div>
  );
}
