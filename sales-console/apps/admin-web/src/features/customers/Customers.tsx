import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  BarChart3,
  CircleDollarSign,
  ClipboardList,
  Copy,
  KeyRound,
  Plus,
  RefreshCw,
  Save,
  Search,
  ShieldCheck,
  Users,
  X,
} from 'lucide-react';
import { api } from '../../api/client';
import { dateTime, money, tokens } from '../../lib/format';
import type { Customer } from '../../types';
import { EmptyState, PageHeading, PageTabs, Panel, Tag } from '../../components/ui';

type CustomerTab = 'profile' | 'orders' | 'usage' | 'technical';
type RechargeOrder = {
  id: string;
  method: string;
  requestedAmount?: number | null;
  amount?: number;
  status: string;
  processedBy?: string;
  createdAt: number;
};
type UsageReport = {
  poolTokens: number;
  totalRecharged: number;
  appVersion: string;
  reportedAt: number;
};

const detailTabs: Array<{ id: CustomerTab; label: string }> = [
  { id: 'profile', label: '基本资料' },
  { id: 'orders', label: '充值记录' },
  { id: 'usage', label: '使用情况' },
  { id: 'technical', label: '技术配置' },
];

function environmentLabel(value: string) {
  return value === 'production' ? '生产' : value === 'test' ? '测试' : '待分类';
}

export function Customers({ refreshToken }: { refreshToken: number }) {
  const [customers, setCustomers] = useState<Customer[]>([]);
  const [selected, setSelected] = useState<Customer | null>(null);
  const [activeTab, setActiveTab] = useState<CustomerTab>('profile');
  const [createOpen, setCreateOpen] = useState(false);
  const [search, setSearch] = useState('');
  const [statusFilter, setStatusFilter] = useState('all');
  const [environmentFilter, setEnvironmentFilter] = useState('all');
  const [name, setName] = useState('');
  const [systemId, setSystemId] = useState('');
  const [ip, setIp] = useState('');
  const [protocol, setProtocol] = useState<'http' | 'https'>('https');
  const [port, setPort] = useState('3443');
  const [internalBaseUrl, setInternalBaseUrl] = useState('');
  const [environment, setEnvironment] = useState('unclassified');
  const [contact, setContact] = useState('');
  const [notes, setNotes] = useState('');
  const [error, setError] = useState('');
  const [token, setToken] = useState('');
  const [tokenKind, setTokenKind] = useState<'api' | 'code'>('api');
  const [amount, setAmount] = useState('1000');
  const [resetUsername, setResetUsername] = useState('');
  const [resetResult, setResetResult] = useState('');
  const [orders, setOrders] = useState<RechargeOrder[]>([]);
  const [usage, setUsage] = useState<UsageReport[]>([]);

  const load = useCallback(() => {
    setError('');
    return api<Customer[]>('/api/v1/customers')
      .then((nextCustomers) => {
        setCustomers(nextCustomers);
        setSelected((current) => {
          if (!current) return current;
          const refreshed = nextCustomers.find((customer) => customer.id === current.id);
          return refreshed ? { ...refreshed, apiToken: current.apiToken } : current;
        });
      })
      .catch((reason) => setError(reason instanceof Error ? reason.message : '读取客户失败'));
  }, []);

  const loadDetail = useCallback(async (customerId: string) => {
    try {
      const [nextOrders, nextUsage] = await Promise.all([
        api<RechargeOrder[]>(`/api/v1/customers/${customerId}/recharge-orders`),
        api<UsageReport[]>(`/api/v1/customers/${customerId}/usage-reports`),
      ]);
      setOrders(nextOrders);
      setUsage(nextUsage);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '客户详情读取失败');
    }
  }, []);

  const selectedId = selected?.id;
  const refresh = useCallback(() => {
    return Promise.all([load(), selectedId ? loadDetail(selectedId) : Promise.resolve()]).then(
      () => undefined,
    );
  }, [load, loadDetail, selectedId]);

  useEffect(() => {
    const timer = window.setTimeout(() => void refresh(), 0);
    return () => window.clearTimeout(timer);
  }, [refresh, refreshToken]);

  useEffect(() => {
    if (!selected && !createOpen) return;
    const handleKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        setSelected(null);
        setCreateOpen(false);
      }
    };
    window.addEventListener('keydown', handleKey);
    return () => window.removeEventListener('keydown', handleKey);
  }, [selected, createOpen]);

  const filteredCustomers = useMemo(() => {
    const query = search.trim().toLocaleLowerCase();
    return customers.filter((customer) => {
      const matchesSearch =
        !query ||
        `${customer.name} ${customer.systemId} ${customer.contact ?? ''}`
          .toLocaleLowerCase()
          .includes(query);
      return (
        matchesSearch &&
        (statusFilter === 'all' || customer.status === statusFilter) &&
        (environmentFilter === 'all' || customer.environment === environmentFilter)
      );
    });
  }, [customers, environmentFilter, search, statusFilter]);

  function selectCustomer(customer: Customer) {
    setSelected(customer);
    setActiveTab('profile');
    setToken('');
    setTokenKind('api');
    setResetResult('');
    setError('');
  }

  function resetCreateForm() {
    setName('');
    setSystemId('');
    setIp('');
    setProtocol('https');
    setPort('3443');
    setInternalBaseUrl('');
    setEnvironment('unclassified');
    setContact('');
    setNotes('');
  }

  async function create() {
    if (!name.trim()) return setError('请填写客户名称');
    setError('');
    try {
      const result = await api<{ customer: Customer; apiTokenOnce: string }>('/api/v1/customers', {
        method: 'POST',
        body: JSON.stringify({
          name: name.trim(),
          system_id: systemId,
          configured_ip: ip,
          internal_base_url: internalBaseUrl,
          protocol,
          port: Number(port),
          environment,
          contact,
          notes,
        }),
      });
      const customer = result.customer;
      setCustomers((items) => [...items, customer]);
      setSelected(customer);
      setActiveTab('technical');
      setToken(result.apiTokenOnce ?? '');
      setTokenKind('api');
      setCreateOpen(false);
      void loadDetail(customer.id);
      resetCreateForm();
      setError('客户已创建。请立即复制一次性 API Token 并交给客户系统配置。');
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '客户创建失败');
    }
  }

  async function issueCode() {
    if (!selected) return;
    setError('');
    try {
      const result = await api<{ order: { code?: string } }>(
        `/api/v1/customers/${selected.id}/recharge-codes`,
        {
          method: 'POST',
          body: JSON.stringify({
            amount: Number(amount).toFixed(2),
            request_id: crypto.randomUUID().replaceAll('-', ''),
          }),
        },
      );
      setToken(result.order.code ?? '');
      setTokenKind('code');
      setError('充值码已生成，请通过安全渠道交付客户。');
      void loadDetail(selected.id);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '充值码生成失败');
    }
  }

  async function reissueCode(orderId: string) {
    if (!selected) return;
    setError('');
    try {
      const result = await api<{ order: { code?: string } }>(
        `/api/v1/customers/${selected.id}/recharge-codes/${orderId}/reissue`,
        { method: 'POST', body: JSON.stringify({}) },
      );
      setToken(result.order.code ?? '');
      setTokenKind('code');
      setError('充值码已重新签发，请交付新码，旧码不要继续使用。');
      void loadDetail(selected.id);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '充值码重新签发失败');
    }
  }

  function updateSelected(patch: Partial<Customer>) {
    setSelected((current) => (current ? { ...current, ...patch } : current));
  }

  async function saveCustomer() {
    if (!selected) return;
    setError('');
    try {
      const result = await api<{ customer: Customer }>(`/api/v1/customers/${selected.id}`, {
        method: 'PATCH',
        body: JSON.stringify({
          name: selected.name,
          system_id: selected.systemId,
          environment: selected.environment,
          configured_ip: selected.ip,
          internal_base_url: selected.internalBaseUrl ?? '',
          port: selected.port,
          protocol: selected.protocol,
          contact: selected.contact,
          notes: selected.notes,
          status: selected.status,
        }),
      });
      const updated = result.customer;
      setCustomers((items) =>
        items.map((item) =>
          item.id === updated.id ? { ...updated, apiToken: selected.apiToken } : item,
        ),
      );
      setSelected({ ...updated, apiToken: selected.apiToken });
      setError('客户资料已保存。');
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '客户资料保存失败');
    }
  }

  async function revealToken() {
    if (!selected) return;
    setError('');
    try {
      const result = await api<{ apiToken: string }>(`/api/customers/${selected.id}/api-token`);
      setToken(result.apiToken);
      setTokenKind('api');
      setError('当前 API Token 已显示。复制后请妥善保存。');
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Token 读取失败');
    }
  }

  async function rotateToken() {
    if (!selected || !window.confirm(`轮换 ${selected.name} 的 API Token？旧 Token 将立即失效。`))
      return;
    setError('');
    try {
      const result = await api<{ apiTokenOnce: string }>(
        `/api/v1/customers/${selected.id}/api-token/rotate`,
        { method: 'POST', body: JSON.stringify({}) },
      );
      setToken(result.apiTokenOnce);
      setTokenKind('api');
      setError('Token 已轮换。请立即复制新 Token 并更新客户系统配置。');
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Token 轮换失败');
    }
  }

  async function resetAdminPassword() {
    if (!selected || !resetUsername.trim()) return setError('请填写要重置的管理员用户名');
    if (!window.confirm(`确认远程重置 ${selected.name} 的管理员“${resetUsername.trim()}”密码？`))
      return;
    setError('');
    try {
      const result = await api<{ username: string; newPassword: string }>(
        `/api/v1/customers/${selected.id}/reset-admin-password`,
        { method: 'POST', body: JSON.stringify({ username: resetUsername.trim() }) },
      );
      setResetResult(`用户名：${result.username}\n新密码：${result.newPassword}`);
      setError('远程密码已重置，请立即记录并通过安全渠道转达客户。');
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '远程重置失败');
    }
  }

  function copySecret(value: string) {
    void navigator.clipboard.writeText(value).then(
      () => setError('已复制到剪贴板。'),
      () => setError('复制失败，请检查浏览器剪贴板权限。'),
    );
  }

  return (
    <div className="page-content">
      <PageHeading
        eyebrow="CUSTOMER FLEET"
        title="客户管理"
        subtitle="搜索客户，查看系统状态或打开详情处理业务。"
      >
        <button type="button" className="ghost-button" onClick={() => void refresh()}>
          <RefreshCw size={15} />
          刷新
        </button>
        <button
          type="button"
          className="primary"
          onClick={() => {
            setError('');
            setCreateOpen(true);
          }}
        >
          <Plus size={16} />
          新建客户
        </button>
      </PageHeading>

      <Panel
        title="客户列表"
        subtitle={`${filteredCustomers.length} / ${customers.length} 套客户系统`}
      >
        <div className="customer-toolbar">
          <label className="search-field customer-search">
            <Search size={16} aria-hidden="true" />
            <input
              value={search}
              onChange={(event) => setSearch(event.target.value)}
              placeholder="搜索客户名称、系统 ID 或联系人"
              aria-label="搜索客户名称、系统 ID 或联系人"
            />
          </label>
          <select
            aria-label="客户状态筛选"
            value={statusFilter}
            onChange={(event) => setStatusFilter(event.target.value)}
          >
            <option value="all">全部状态</option>
            <option value="active">正常</option>
            <option value="disabled">已停用</option>
          </select>
          <select
            aria-label="客户环境筛选"
            value={environmentFilter}
            onChange={(event) => setEnvironmentFilter(event.target.value)}
          >
            <option value="all">全部环境</option>
            <option value="production">生产</option>
            <option value="test">测试</option>
            <option value="unclassified">待分类</option>
          </select>
          <span className="toolbar-count">共 {customers.length} 套</span>
        </div>
        {error && !selected && <p className="form-error">{error}</p>}
        {filteredCustomers.length ? (
          <div className="table-wrap customer-table-wrap">
            <table className="customer-table">
              <thead>
                <tr>
                  <th>客户 / 系统 ID</th>
                  <th>环境 / 连接状态</th>
                  <th>最近上报</th>
                  <th>Token 池</th>
                  <th>累计充值</th>
                  <th>状态</th>
                </tr>
              </thead>
              <tbody>
                {filteredCustomers.map((customer) => (
                  <tr
                    className={`customer-table-row ${selected?.id === customer.id ? 'selected-row' : ''}`}
                    key={customer.id}
                    onClick={() => selectCustomer(customer)}
                    tabIndex={0}
                    onKeyDown={(event) => {
                      if (event.key === 'Enter' || event.key === ' ') {
                        event.preventDefault();
                        selectCustomer(customer);
                      }
                    }}
                    aria-label={`查看 ${customer.name} 客户详情`}
                  >
                    <td>
                      <b>{customer.name}</b>
                      <small>{customer.systemId || '未登记系统 ID'}</small>
                    </td>
                    <td>
                      <span>{environmentLabel(customer.environment)}</span>
                      <small>
                        {customer.online ? '近期上报' : customer.lastReport ? '上报过期' : '未接入'}
                      </small>
                    </td>
                    <td>{dateTime(customer.lastSeenAt ?? customer.lastReport?.reportedAt)}</td>
                    <td>{tokens(customer.lastReport?.poolTokens)}</td>
                    <td>{money(customer.totalRecharged)}</td>
                    <td>
                      <Tag
                        good={customer.status === 'active'}
                        text={customer.status === 'active' ? '正常' : '已停用'}
                      />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <EmptyState
            icon={<Users size={19} />}
            title={customers.length ? '没有匹配的客户' : '暂无客户系统'}
            detail={
              customers.length
                ? '调整搜索条件或筛选项。'
                : '创建客户后，系统状态和充值记录会显示在这里。'
            }
          />
        )}
      </Panel>

      {createOpen && (
        <div
          className="modal-backdrop"
          onMouseDown={(event) => {
            if (event.target === event.currentTarget) setCreateOpen(false);
          }}
        >
          <section
            className="create-dialog"
            role="dialog"
            aria-modal="true"
            aria-labelledby="create-customer-title"
          >
            <header className="dialog-heading">
              <div>
                <span className="eyebrow">CUSTOMER SETUP</span>
                <h2 id="create-customer-title">新建客户</h2>
                <p>创建后 API Token 仅首次返回，请交给客户系统配置。</p>
              </div>
              <button
                type="button"
                className="icon-button"
                aria-label="关闭新建客户"
                onClick={() => setCreateOpen(false)}
              >
                <X size={18} />
              </button>
            </header>
            {error && <p className="form-error">{error}</p>}
            <div className="create-form-grid">
              <label>
                客户名称 <em>*</em>
                <input
                  autoFocus
                  value={name}
                  onChange={(event) => setName(event.target.value)}
                  placeholder="例如：华东客户"
                />
              </label>
              <label>
                系统 ID
                <input
                  value={systemId}
                  onChange={(event) => setSystemId(event.target.value)}
                  placeholder="可稍后补充"
                />
              </label>
              <label>
                配置 IP
                <input
                  value={ip}
                  onChange={(event) => setIp(event.target.value)}
                  placeholder="可选"
                />
              </label>
              <label>
                内部连接地址
                <input
                  value={internalBaseUrl}
                  onChange={(event) => setInternalBaseUrl(event.target.value)}
                  placeholder="可选，例如 http://host:8001"
                />
              </label>
              <label>
                协议
                <select
                  value={protocol}
                  onChange={(event) => {
                    const next = event.target.value as 'http' | 'https';
                    setProtocol(next);
                    setPort(next === 'https' ? '3443' : '3000');
                  }}
                >
                  <option value="https">HTTPS</option>
                  <option value="http">HTTP</option>
                </select>
              </label>
              <label>
                端口
                <input
                  type="number"
                  value={port}
                  onChange={(event) => setPort(event.target.value)}
                />
              </label>
              <label>
                环境
                <select
                  value={environment}
                  onChange={(event) => setEnvironment(event.target.value)}
                >
                  <option value="unclassified">待分类</option>
                  <option value="test">测试</option>
                  <option value="production">生产</option>
                </select>
              </label>
              <label>
                联系人
                <input value={contact} onChange={(event) => setContact(event.target.value)} />
              </label>
              <label className="create-form-wide">
                备注
                <textarea
                  rows={3}
                  value={notes}
                  onChange={(event) => setNotes(event.target.value)}
                />
              </label>
            </div>
            <footer className="dialog-actions">
              <button type="button" className="secondary" onClick={() => setCreateOpen(false)}>
                取消
              </button>
              <button
                type="button"
                className="primary"
                onClick={() => void create()}
                disabled={!name.trim()}
              >
                <Users size={15} />
                创建客户
              </button>
            </footer>
          </section>
        </div>
      )}

      {selected && (
        <div
          className="drawer-backdrop"
          onMouseDown={(event) => {
            if (event.target === event.currentTarget) setSelected(null);
          }}
        >
          <aside
            className="customer-drawer"
            role="dialog"
            aria-modal="true"
            aria-labelledby="customer-drawer-title"
          >
            <header className="drawer-heading">
              <div>
                <span className="eyebrow">CUSTOMER PROFILE</span>
                <h2 id="customer-drawer-title">{selected.name}</h2>
                <p>
                  {selected.systemId || '未登记系统 ID'} · {environmentLabel(selected.environment)}
                </p>
              </div>
              <button
                type="button"
                className="icon-button"
                aria-label="关闭客户详情"
                onClick={() => setSelected(null)}
              >
                <X size={18} />
              </button>
            </header>
            {error && (
              <p className="drawer-message" role="status">
                {error}
              </p>
            )}
            <PageTabs
              label="客户详情分类"
              tabs={detailTabs}
              value={activeTab}
              onChange={setActiveTab}
            />
            <div className="drawer-content">
              {activeTab === 'profile' && (
                <section className="drawer-section">
                  <div className="drawer-section-heading">
                    <div>
                      <h3>基本资料</h3>
                      <p>更新客户信息与连接参数</p>
                    </div>
                    <Tag
                      good={selected.status === 'active'}
                      text={selected.status === 'active' ? '正常' : '已停用'}
                    />
                  </div>
                  <div className="drawer-form-grid">
                    <label>
                      客户名称
                      <input
                        value={selected.name}
                        onChange={(event) => updateSelected({ name: event.target.value })}
                      />
                    </label>
                    <label>
                      系统 ID
                      <input
                        value={selected.systemId}
                        onChange={(event) => updateSelected({ systemId: event.target.value })}
                      />
                    </label>
                    <label>
                      环境
                      <select
                        value={selected.environment}
                        onChange={(event) => updateSelected({ environment: event.target.value })}
                      >
                        <option value="unclassified">待分类</option>
                        <option value="test">测试</option>
                        <option value="production">生产</option>
                      </select>
                    </label>
                    <label>
                      状态
                      <select
                        value={selected.status}
                        onChange={(event) => updateSelected({ status: event.target.value })}
                      >
                        <option value="active">正常</option>
                        <option value="disabled">已停用</option>
                      </select>
                    </label>
                    <label>
                      连接协议
                      <select
                        value={selected.protocol}
                        onChange={(event) =>
                          updateSelected({
                            protocol: event.target.value as 'http' | 'https',
                            port: event.target.value === 'https' ? 3443 : 3000,
                          })
                        }
                      >
                        <option value="https">HTTPS</option>
                        <option value="http">HTTP</option>
                      </select>
                    </label>
                    <label>
                      端口
                      <input
                        type="number"
                        value={selected.port}
                        onChange={(event) => updateSelected({ port: Number(event.target.value) })}
                      />
                    </label>
                    <label>
                      配置 IP
                      <input
                        value={selected.ip}
                        placeholder="例如 192.168.33.109"
                        onChange={(event) => updateSelected({ ip: event.target.value })}
                      />
                    </label>
                    <label>
                      联系人
                      <input
                        value={selected.contact ?? ''}
                        onChange={(event) => updateSelected({ contact: event.target.value })}
                      />
                    </label>
                    <label className="drawer-form-wide">
                      内部连接地址
                      <input
                        value={selected.internalBaseUrl ?? ''}
                        placeholder="可选，例如 http://host:8001"
                        onChange={(event) =>
                          updateSelected({ internalBaseUrl: event.target.value || null })
                        }
                      />
                      <small>服务器优先使用该地址，网络失败时回退到配置 IP。</small>
                    </label>
                    <label className="drawer-form-wide">
                      备注
                      <textarea
                        rows={3}
                        value={selected.notes ?? ''}
                        onChange={(event) => updateSelected({ notes: event.target.value })}
                      />
                    </label>
                  </div>
                  <div className="connection-facts">
                    <div>
                      <small>最近实际来源 IP</small>
                      <b>{selected.lastSeenIP || '暂无上报记录'}</b>
                    </div>
                    <div>
                      <small>最近上报时间</small>
                      <b>{dateTime(selected.lastSeenAt ?? selected.lastReport?.reportedAt)}</b>
                    </div>
                  </div>
                  {selected.ip && selected.lastSeenIP && selected.ip !== selected.lastSeenIP && (
                    <p className="form-error">
                      最近来源 IP 与配置 IP 不一致，请核实客户系统的实际出口地址。
                    </p>
                  )}
                  <div className="drawer-footer">
                    <button className="primary" type="button" onClick={() => void saveCustomer()}>
                      <Save size={15} />
                      保存客户资料
                    </button>
                  </div>
                </section>
              )}

              {activeTab === 'orders' && (
                <section className="drawer-section">
                  <div className="drawer-section-heading">
                    <div>
                      <h3>充值记录</h3>
                      <p>在线申请与离线充值码的统一时间线</p>
                    </div>
                  </div>
                  <div className="issue-code-row">
                    <label>
                      签发金额（元）
                      <input
                        type="number"
                        min="1"
                        value={amount}
                        onChange={(event) => setAmount(event.target.value)}
                      />
                    </label>
                    <button
                      type="button"
                      className="primary"
                      disabled={!selected.systemId}
                      onClick={() => void issueCode()}
                    >
                      <CircleDollarSign size={15} />
                      生成充值码
                    </button>
                  </div>
                  {!selected.systemId && (
                    <p className="inline-note">请先登记系统 ID，才能签发充值码。</p>
                  )}
                  {token && tokenKind === 'code' && (
                    <div className="secret-result">
                      <div>
                        <b>一次性充值码</b>
                        <small>请安全交付；重签后旧码不可继续使用。</small>
                      </div>
                      <code>{token}</code>
                      <button
                        className="secondary compact-button"
                        onClick={() => copySecret(token)}
                      >
                        <Copy size={14} />
                        复制
                      </button>
                    </div>
                  )}
                  {orders.length ? (
                    <div className="table-wrap">
                      <table>
                        <thead>
                          <tr>
                            <th>时间</th>
                            <th>方式</th>
                            <th>申请金额</th>
                            <th>入账金额</th>
                            <th>状态</th>
                            <th>经手人</th>
                            <th>操作</th>
                          </tr>
                        </thead>
                        <tbody>
                          {[...orders]
                            .sort((a, b) => b.createdAt - a.createdAt)
                            .map((order) => (
                              <tr key={order.id}>
                                <td>{dateTime(order.createdAt)}</td>
                                <td>{order.method === 'online' ? '在线申请' : '充值码'}</td>
                                <td>
                                  {order.requestedAmount ? money(order.requestedAmount) : '—'}
                                </td>
                                <td>{order.amount ? money(order.amount) : '—'}</td>
                                <td>
                                  <Tag
                                    good={order.status === 'approved' || order.status === 'issued'}
                                    text={order.status}
                                  />
                                </td>
                                <td>{order.processedBy ?? '—'}</td>
                                <td>
                                  {order.method === 'code' && order.status === 'issued' ? (
                                    <button
                                      className="secondary compact-button"
                                      onClick={() => void reissueCode(order.id)}
                                    >
                                      <RefreshCw size={13} />
                                      重签
                                    </button>
                                  ) : (
                                    '—'
                                  )}
                                </td>
                              </tr>
                            ))}
                        </tbody>
                      </table>
                    </div>
                  ) : (
                    <EmptyState
                      icon={<ClipboardList size={18} />}
                      title="暂无充值记录"
                      detail="申请或签发充值后会出现在这里。"
                    />
                  )}
                </section>
              )}

              {activeTab === 'usage' && (
                <section className="drawer-section">
                  <div className="drawer-section-heading">
                    <div>
                      <h3>使用情况</h3>
                      <p>最近 10 次客户系统汇总快照</p>
                    </div>
                  </div>
                  {usage.length ? (
                    <div className="table-wrap">
                      <table>
                        <thead>
                          <tr>
                            <th>上报时间</th>
                            <th>剩余 Token</th>
                            <th>累计充值</th>
                            <th>应用版本</th>
                          </tr>
                        </thead>
                        <tbody>
                          {usage
                            .slice(-10)
                            .reverse()
                            .map((report) => (
                              <tr key={report.reportedAt}>
                                <td>{dateTime(report.reportedAt)}</td>
                                <td>{tokens(report.poolTokens)}</td>
                                <td>{money(report.totalRecharged)}</td>
                                <td>{report.appVersion || '—'}</td>
                              </tr>
                            ))}
                        </tbody>
                      </table>
                    </div>
                  ) : (
                    <EmptyState
                      icon={<BarChart3 size={18} />}
                      title="暂无上报数据"
                      detail="客户系统完成定时同步后显示余额和版本。"
                    />
                  )}
                </section>
              )}

              {activeTab === 'technical' && (
                <section className="drawer-section">
                  <div className="drawer-section-heading">
                    <div>
                      <h3>技术配置</h3>
                      <p>API Token 与远程支持操作</p>
                    </div>
                  </div>
                  <div className="technical-card">
                    <div className="technical-card-heading">
                      <KeyRound size={17} />
                      <div>
                        <b>客户 API Token</b>
                        <small>轮换后旧 Token 会立即失效</small>
                      </div>
                    </div>
                    <div className="action-row compact-actions">
                      <button className="secondary" onClick={() => void revealToken()}>
                        <KeyRound size={14} />
                        显示现有 Token
                      </button>
                      <button className="danger" onClick={() => void rotateToken()}>
                        <RefreshCw size={14} />
                        轮换 Token
                      </button>
                    </div>
                    {token && tokenKind === 'api' && (
                      <div className="secret-result">
                        <code>{token}</code>
                        <button
                          className="secondary compact-button"
                          onClick={() => copySecret(token)}
                        >
                          <Copy size={14} />
                          复制
                        </button>
                      </div>
                    )}
                    <p className="inline-note">
                      客户系统使用此 Token 向销售总台认证。新建或轮换后只展示一次，请立即交付。
                    </p>
                  </div>
                  <div className="technical-card">
                    <div className="technical-card-heading">
                      <ShieldCheck size={17} />
                      <div>
                        <b>远程管理员支持</b>
                        <small>仅在客户 VM 可访问时使用</small>
                      </div>
                    </div>
                    <label>
                      要重置的管理员用户名
                      <input
                        placeholder="例如 cnhh"
                        value={resetUsername}
                        onChange={(event) => setResetUsername(event.target.value)}
                      />
                    </label>
                    <button
                      className="danger"
                      disabled={!selected.ip}
                      onClick={() => void resetAdminPassword()}
                    >
                      <KeyRound size={14} />
                      重置客户管理员密码
                    </button>
                    {!selected.ip && (
                      <p className="inline-note">该客户未配置 IP，当前不可远程连接。</p>
                    )}
                    {resetResult && (
                      <div className="secret-result sensitive-result">
                        <code>{resetResult}</code>
                        <small>临时密码仅显示本次，请使用安全渠道转达并提醒客户修改。</small>
                      </div>
                    )}
                  </div>
                </section>
              )}
            </div>
          </aside>
        </div>
      )}
    </div>
  );
}
