import { useState } from 'react';
import { LayoutDashboard, Share2, Bot, FileSearch, Settings, PanelLeftClose, PanelLeftOpen, Check } from 'lucide-react';
import { NavLink } from 'react-router-dom';
import { cn } from '@/lib/utils';
import { useStore, type PhaseStatus } from '@/store';
import { PHASE_CONFIG, NUM_PHASES } from '@/lib/phaseConfig';
import { PHASE_COLOR } from '@/lib/nodeColors';

// ── Status indicator ─────────────────────────────────────────────────────────

const STATUS_RING: Record<PhaseStatus, string> = {
  pending:           'border-slate-600/50',
  active:            'border-amber-400 animate-pulse',
  complete:          'border-green-500 bg-green-500',
  awaiting_approval: 'border-blue-400',
  skipped:           'border-slate-700/30',
};

function StatusIndicator({ status, size = 'sm' }: { status: PhaseStatus | undefined; size?: 'sm' | 'md' }) {
  const s = status ?? 'pending';
  const dim = size === 'md' ? 'w-3.5 h-3.5' : 'w-2 h-2';
  const isComplete = s === 'complete';

  return (
    <span className={cn(
      'rounded-full shrink-0 border flex items-center justify-center',
      dim,
      STATUS_RING[s] ?? STATUS_RING.pending,
    )}>
      {isComplete && <Check size={size === 'md' ? 8 : 6} className="text-white" strokeWidth={3} />}
    </span>
  );
}

// ── NavItem helper ────────────────────────────────────────────────────────────

function NavItem({ to, icon, label, collapsed, end }: {
  to: string; icon: React.ReactNode; label: string; collapsed: boolean; end?: boolean;
}) {
  return (
    <NavLink
      to={to}
      end={end}
      title={collapsed ? label : undefined}
      className={({ isActive }) => cn(
        'flex items-center gap-2.5 px-3 py-2 rounded-lg text-sm font-medium transition-all',
        collapsed && 'justify-center px-2',
        isActive
          ? 'bg-forge-accent/10 text-forge-accent'
          : 'text-forge-muted hover:text-forge-text hover:bg-forge-border/50',
      )}
    >
      {icon}
      {!collapsed && <span className="truncate">{label}</span>}
    </NavLink>
  );
}

// ── Main Sidebar ──────────────────────────────────────────────────────────────

export function Sidebar() {
  const { phases } = useStore();
  const [collapsed, setCollapsed] = useState(() =>
    localStorage.getItem('sidebar.collapsed') === 'true',
  );

  const toggle = () => {
    setCollapsed(prev => {
      localStorage.setItem('sidebar.collapsed', String(!prev));
      return !prev;
    });
  };

  return (
    <aside className={`${collapsed ? 'w-12' : 'w-56'} border-r border-forge-border bg-forge-surface flex flex-col transition-[width] duration-200 shrink-0`}>
      {/* Logo */}
      <NavLink to="/" className="h-14 flex items-center px-3 border-b border-forge-border shrink-0 hover:bg-forge-border/30 transition-colors">
        <div className="w-6 h-6 bg-forge-accent rounded-md shrink-0" />
        {!collapsed && (
          <span className="font-bold font-mono text-forge-text tracking-wider ml-3 truncate">
            FORGE <span className="text-forge-muted/50 font-normal text-xs tracking-normal">V0.1 Beta</span>
          </span>
        )}
      </NavLink>

      {/* Navigation */}
      <nav className="flex-1 overflow-y-auto p-2 space-y-0.5">
        <NavItem to="/" icon={<LayoutDashboard size={16} />} label="Command Centre" collapsed={collapsed} end />
        <NavItem to="/graph-inspector" icon={<Share2 size={16} />} label="Graph Inspector" collapsed={collapsed} />
        <NavItem to="/agent-inspector" icon={<Bot size={16} />} label="Agent Inspector" collapsed={collapsed} />
        <NavItem to="/logs" icon={<FileSearch size={16} />} label="Logs" collapsed={collapsed} />

        {/* Phases section */}
        {!collapsed && (
          <div className="pt-3 pb-1">
            <p className="px-3 text-[9px] font-mono uppercase tracking-widest text-forge-muted/40">Phases</p>
          </div>
        )}
        {collapsed && <div className="py-1.5 border-t border-forge-border/50 my-1" />}

        {Array.from({ length: NUM_PHASES }, (_, i) => {
          const config = PHASE_CONFIG[i];
          const Icon = config.icon;
          const status = phases[i]?.status;
          return (
            <NavLink
              key={i}
              to={`/phase/${i}`}
              title={collapsed ? `${i} ${config.name} — ${status ?? 'pending'}` : undefined}
              className={({ isActive }) => cn(
                'flex items-center gap-2 px-3 py-1.5 rounded-lg text-sm font-medium transition-all',
                collapsed && 'justify-center px-2',
                isActive
                  ? 'bg-forge-accent/10 text-forge-accent'
                  : 'text-forge-muted hover:text-forge-text hover:bg-forge-border/50',
              )}
            >
              {collapsed ? (
                /* Collapsed: icon with status ring overlay */
                <div className="relative">
                  <Icon size={15} className="shrink-0" style={{ color: PHASE_COLOR[i] }} />
                  <span className="absolute -top-0.5 -right-0.5">
                    <StatusIndicator status={status} />
                  </span>
                </div>
              ) : (
                /* Expanded: icon + name + status */
                <>
                  <Icon size={15} className="shrink-0" style={{ color: PHASE_COLOR[i] }} />
                  <span className="flex-1 truncate text-xs">{i} {config.name}</span>
                  <StatusIndicator status={status} size="md" />
                </>
              )}
            </NavLink>
          );
        })}
      </nav>

      {/* Footer */}
      <div className="p-2 border-t border-forge-border shrink-0 space-y-0.5">
        <NavItem to="/settings" icon={<Settings size={16} />} label="Settings" collapsed={collapsed} />
        <button
          onClick={toggle}
          title={collapsed ? 'Expand sidebar' : 'Collapse sidebar'}
          className={cn(
            'flex items-center gap-2.5 px-3 py-2 w-full rounded-lg text-sm font-medium text-forge-muted hover:text-forge-text hover:bg-forge-border/50 transition-all',
            collapsed && 'justify-center px-2',
          )}
        >
          {collapsed ? <PanelLeftOpen size={16} /> : <PanelLeftClose size={16} />}
          {!collapsed && <span>Collapse</span>}
        </button>
      </div>
    </aside>
  );
}
