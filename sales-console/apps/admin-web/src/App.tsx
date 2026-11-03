import { useEffect, useMemo, useState } from 'react';
import {
  ChevronRight,
  CircleDollarSign,
  LayoutDashboard,
  LogOut,
  Package,
  RefreshCw,
  Settings2,
  ShieldCheck,
  Users,
} from 'lucide-react';
import { api } from './api/client';
import { Loading } from './components/ui';
import { Login } from './features/auth/Login';
import { Overview } from './features/dashboard/Overview';
import { Customers } from './features/customers/Customers';
import { Finance } from './features/finance/Finance';
import { Upgrades } from './features/upgrades/Upgrades';
import { SystemManagement } from './features/settings/SystemManagement';
import type { FinanceSection, Staff, SystemSection, UpgradeSummary, View } from './types';

const nav: Array<{ id: View; label: string; icon: typeof LayoutDashboard }> = [
  { id: 'overview', label: '工作总览', icon: LayoutDashboard },
  { id: 'customers', label: '客户管理', icon: Users },
  { id: 'finance', label: '充值与财务', icon: CircleDollarSign },
  { id: 'upgrades', label: '版本与升级', icon: Package },
  { id: 'system', label: '系统管理', icon: Settings2 },
];

export default function App() {
  const [staff, setStaff] = useState<Staff | null>(null);
  const [view, setView] = useState<View>('overview');
  const [financeSection, setFinanceSection] = useState<FinanceSection>('requests');
  const [systemSection, setSystemSection] = useState<SystemSection>('business');
  const [upgradeSummary, setUpgradeSummary] = useState<UpgradeSummary | null>(null);
  const [refreshToken, setRefreshToken] = useState(0);
  const [checking, setChecking] = useState(true);
  useEffect(() => {
    api<{ authenticated: boolean; staff: Staff | null }>('/api/v1/auth/me')
      .then((result) => setStaff(result.staff))
      .catch(() => setStaff(null))
      .finally(() => setChecking(false));
  }, []);
  const current = useMemo(() => nav.find((item) => item.id === view) ?? nav[0], [view]);
  if (checking) return <Loading />;
  if (!staff) return <Login onLoggedIn={setStaff} />;
  async function logout() {
    await api('/api/v1/auth/logout', { method: 'POST' }).catch(() => undefined);
    setStaff(null);
  }
  function navigate(next: View, section?: FinanceSection | SystemSection) {
    setView(next);
    if (next === 'finance' && section) setFinanceSection(section as FinanceSection);
    if (next === 'system' && section) setSystemSection(section as SystemSection);
  }
  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand">
          <span className="brand-mark">
            <img src="/longxin-logo.png" alt="龙信" />
          </span>
          <span className="brand-copy">
            <strong>龙信</strong>
            <span>销售运营中心</span>
          </span>
        </div>
        <div className="sidebar-label">销售运营</div>
        <nav className="nav-list">
          {nav.map(({ id, label, icon: Icon }) => (
            <button
              type="button"
              aria-current={view === id ? 'page' : undefined}
              className={`nav-item ${view === id ? 'active' : ''}`}
              key={id}
              onClick={() => navigate(id)}
            >
              <Icon size={17} />
              <span className="nav-copy">
                <strong>{label}</strong>
              </span>
              {view === id && <i aria-hidden="true" />}
            </button>
          ))}
        </nav>
        <div className="sidebar-bottom">
          <div className="user-chip">
            <span className="avatar small-avatar">{staff.username.slice(0, 1).toUpperCase()}</span>
            <span>
              <b>{staff.username}</b>
              <small>销售管理员</small>
            </span>
          </div>
          <button className="logout-button" onClick={logout}>
            <LogOut size={16} />
            退出登录
          </button>
        </div>
      </aside>
      <main className="main-area">
        <div className="topbar">
          <span className="breadcrumb">
            运营工作台 <ChevronRight size={14} /> {current.label}
          </span>
          <div className="topbar-actions">
            <button
              className="ghost-button topbar-refresh"
              type="button"
              title="刷新当前页面数据"
              aria-label="刷新当前页面数据"
              onClick={() => setRefreshToken((value) => value + 1)}
            >
              <RefreshCw size={15} />
              刷新数据
            </button>
            <span className="secure-badge">
              <ShieldCheck size={14} /> 安全连接
            </span>
          </div>
        </div>
        {view === 'overview' && (
          <Overview
            onNavigate={navigate}
            refreshToken={refreshToken}
            upgradeSummary={upgradeSummary}
          />
        )}
        {view === 'customers' && <Customers refreshToken={refreshToken} />}
        {view === 'finance' && (
          <Finance
            refreshToken={refreshToken}
            activeSection={financeSection}
            onSectionChange={setFinanceSection}
          />
        )}
        {view === 'upgrades' && (
          <Upgrades refreshToken={refreshToken} onSummaryChange={setUpgradeSummary} />
        )}
        {view === 'system' && (
          <SystemManagement
            refreshToken={refreshToken}
            activeSection={systemSection}
            onSectionChange={setSystemSection}
          />
        )}
      </main>
    </div>
  );
}
