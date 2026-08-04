import { BrowserRouter, Routes, Route } from 'react-router-dom';
import { QueryClientProvider } from '@tanstack/react-query';
import { queryClient } from '@/queryClient';
import { Layout } from '@/components/Layout';
import { LoginPage } from '@/components/LoginPage';
import { CommandCentre } from '@/dashboards/CommandCentre';
import { PhaseDashboard } from '@/dashboards/PhaseDashboard';
import { GraphInspector } from '@/dashboards/GraphInspector';
import { AgentInspector } from '@/dashboards/AgentInspector';
import { LogViewer } from '@/dashboards/LogViewer';
import { Settings } from '@/dashboards/Settings';
import { useAuth } from '@/hooks/useAuth';
import { RefreshCw } from 'lucide-react';

function AuthGate() {
  const { loading, authenticated, authRequired, recheck } = useAuth();

  if (loading) {
    return (
      <div className="min-h-screen bg-forge-bg flex items-center justify-center">
        <RefreshCw size={20} className="text-forge-muted animate-spin" />
      </div>
    );
  }

  if (authRequired && !authenticated) {
    return <LoginPage onSuccess={recheck} />;
  }

  return (
    <BrowserRouter>
      <Routes>
        <Route path="/" element={<Layout />}>
          <Route index element={<CommandCentre />} />
          <Route path="phase/:phaseNum" element={<PhaseDashboard />} />
          <Route path="graph-inspector" element={<GraphInspector />} />
          <Route path="agent-inspector" element={<AgentInspector />} />
          <Route path="logs" element={<LogViewer />} />
          <Route path="settings" element={<Settings />} />
        </Route>
      </Routes>
    </BrowserRouter>
  );
}

export function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <AuthGate />
    </QueryClientProvider>
  );
}
