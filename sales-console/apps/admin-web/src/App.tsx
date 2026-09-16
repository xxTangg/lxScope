import { useCallback, useEffect, useMemo, useState, type FormEvent, type ReactNode } from 'react';
import {
  AlertTriangle,
  ArrowUpRight,
  BarChart3,
  Check,
  ChevronRight,
  CircleDollarSign,
  ClipboardList,
  Copy,
  Gauge,
  KeyRound,
  LayoutDashboard,
  LogOut,
  MonitorCog,
  Package,
  RefreshCw,
  Save,
  Settings2,
  ShieldCheck,
  Users,
  X,
} from 'lucide-react';

type View =
  | 'overview'
  | 'customers'
  | 'requests'
  | 'reconciliation'
  | 'alerts'
  | 'publickey'
  | 'upgrades'
  | 'settings';
type Staff = { id: string; username: string; role: string };
type Customer = {
  id: string;
  name: string;
  systemId: string;
  environment: string;
  ip: string;
  port: number;
  protocol: 'http' | 'https';
  baseUrl?: string | null;
  contact?: string;
  notes?: string;
  status: string;
  totalRecharged: number;
  apiToken?: string;
  lastSeenIP?: string | null;
  lastSeenAt?: number | null;
  lastReport?: { poolTokens: number; appVersion: string; reportedAt: number } | null;
  online?: boolean;
  stale?: boolean;
  tier?: { admins: number; accounts: number };
};
type Dashboard = {
  generatedAt: number;
  totalCustomers: number;
  online: number;
  pending: number;
  awaitingDelivery: number;
  totalRecharged: number;
  recharged30d: number;
  poolRemaining: number;
  days: Array<{ at: number; amount: number }>;
  customers: Customer[];
  activities: Array<{
    id: string;
    name: string;
    amount: number;
    status: string;
    delivered: boolean;
    at: number;
  }>;
};
type ReleaseMeta = {
  type: string;
  version: string;
  file: string;
  size: number;
  uploadedAt: number;
  uploadedBy: string;
} | null;
type ReleaseType = 'app' | 'opencode';

const releaseLabels: Record<ReleaseType, string> = {
  app: '龙信业务应用升级包',
  opencode: 'AgentScope 平台升级包',
};
const releaseTypes: ReleaseType[] = ['opencode', 'app'];

function fromCanonical(value: unknown, key?: string): unknown {
  if (Array.isArray(value)) return value.map((item) => fromCanonical(item));
  if (!value || typeof value !== 'object') {
    if (
      typeof value === 'string' &&
      key &&
      /(?:^|_)(?:at|created_at|updated_at|reported_at|processed_at|delivered_at|last_delivery_at|uploaded_at|last_seen_at|last_report_at|issued_at|expires_at|checked_at)$/.test(
        key,
      )
    ) {
      const parsed = Date.parse(value);
      return Number.isNaN(parsed) ? value : parsed;
    }
    if (
      typeof value === 'string' &&
      ['amount', 'requested_amount', 'total_recharged', 'recharged_amount', 'recharged_30d'].includes(
        key ?? '',
      )
    ) {
      const parsed = Number(value);
      return Number.isFinite(parsed) ? parsed : value;
    }
    return value;
  }
  const converted = Object.fromEntries(
    Object.entries(value).map(([itemKey, item]) => {
      const camelKey = itemKey.replace(/_([a-z])/g, (_match, letter: string) => letter.toUpperCase());
      return [camelKey, fromCanonical(item, itemKey)];
    }),
  );
  return converted;
}

function fromCanonicalCustomer(value: unknown): Customer {
  const customer = fromCanonical(value) as Record<string, any>;
  return {
    ...customer,
    id: customer.customerId ?? customer.id,
    systemId: customer.systemId ?? '',
    ip: customer.configuredIp ?? customer.ip ?? '',
    baseUrl: customer.baseUrl ?? null,
    lastSeenIP: customer.lastSourceIp ?? customer.lastSeenIP ?? null,
    lastSeenAt: customer.lastReportAt ?? customer.lastSeenAt ?? null,
    totalRecharged: Number(customer.totalRecharged ?? 0),
    apiToken: customer.apiToken,
  } as Customer;
}

function canonicalReleaseType(type: ReleaseType): string {
  return type === 'opencode' ? 'core' : 'app';
}

function normalizeCanonicalResponse<T>(path: string, value: unknown): T {
  const converted = fromCanonical(value) as any;
  if (converted?.staff) {
    converted.staff = { ...converted.staff, id: converted.staff.staffId ?? converted.staff.id };
  }
  if (path === '/api/v1/dashboard' && Array.isArray(converted?.customers)) {
    converted.customers = converted.customers.map(fromCanonicalCustomer);
    converted.activities = (converted.activities ?? []).map((item: any) => ({
      ...item,
      id: item.activityId ?? item.id,
    }));
    return converted as T;
  }
  if (path === '/api/v1/customers' && Array.isArray(converted?.customers)) {
    return converted.customers.map(fromCanonicalCustomer) as T;
  }
  if (/^\/api\/v1\/customers\/[^/]+$/.test(path) && converted?.customer) {
    return { ...converted, customer: fromCanonicalCustomer(converted.customer) } as T;
  }
  if (/^\/api\/v1\/customers\/[^/]+\/(?:usage-reports|recharge-orders)$/.test(path)) {
    if (Array.isArray(converted?.reports)) return converted.reports as T;
    if (Array.isArray(converted?.orders)) {
      return converted.orders.map((item: any) => ({ ...item, id: item.orderId ?? item.id })) as T;
    }
  }
  if (path === '/api/v1/recharge-requests' && Array.isArray(converted?.orders)) {
    return converted.orders.map((item: any) => ({ ...item, id: item.orderId ?? item.id })) as T;
  }
  if (path === '/api/v1/reconciliation' && Array.isArray(converted?.items)) return converted.items as T;
  if (path === '/api/v1/alerts' && Array.isArray(converted?.alerts)) return converted.alerts as T;
  if (path === '/api/v1/audit/events' && Array.isArray(converted?.events)) return converted.events as T;
  if (path === '/api/v1/releases' && converted && 'core' in converted) {
    return { ...converted, opencode: converted.core } as T;
  }
  if (/^\/api\/v1\/releases\/(?:app|core)$/.test(path) && converted?.type === 'core') {
    return { ...converted, type: 'opencode' } as T;
  }
  if (/^\/api\/v1\/upgrade-all\/(?:app|core)$/.test(path) && Array.isArray(converted?.results)) {
    return {
      ...converted,
      results: converted.results.map((item: any) => ({ ...item, id: item.customerId ?? item.id })),
    } as T;
  }
  if (path === '/api/v1/customers' && converted?.customer) {
    return { ...converted, customer: fromCanonicalCustomer(converted.customer) } as T;
  }
  return converted as T;
}

const nav: Array<{ id: View; label: string; icon: typeof LayoutDashboard; hint: string }> = [
  { id: 'overview', label: '经营总览', icon: LayoutDashboard, hint: '客户、额度与待办' },
  { id: 'customers', label: '客户系统', icon: Users, hint: '客户档案与额度' },
  { id: 'requests', label: '充值审核', icon: ClipboardList, hint: '在线充值申请' },
  { id: 'reconciliation', label: '对账中心', icon: BarChart3, hint: '充值与消耗' },
  { id: 'alerts', label: '余额预警', icon: AlertTriangle, hint: '续费关注' },
  { id: 'upgrades', label: '升级管理', icon: Package, hint: '版本发布与推送' },
  { id: 'publickey', label: '总部公钥', icon: KeyRound, hint: '客户系统配置' },
  { id: 'settings', label: '系统设置', icon: Settings2, hint: '汇率、审计与安全' },
];

async function api<T>(url: string, options: RequestInit = {}): Promise<T> {
  const path = url.split('?')[0];
  const headers = new Headers(options.headers);
  if (!(options.body instanceof FormData)) headers.set('Content-Type', 'application/json');
  if (!['GET', 'HEAD', 'OPTIONS'].includes(options.method?.toUpperCase() ?? 'GET')) {
    if (!['/api/v1/auth/login', '/api/v1/auth/logout'].includes(path)) {
      headers.set('Idempotency-Key', headers.get('Idempotency-Key') ?? crypto.randomUUID());
    }
  }
  headers.set('X-Request-ID', headers.get('X-Request-ID') ?? crypto.randomUUID());
  const response = await fetch(url, { ...options, headers, credentials: 'include' });
  if (!response.ok) {
    const body = (await response.json().catch(() => ({}))) as {
      error?: string;
      detail?: { message?: string };
    };
    throw new Error(body.detail?.message ?? body.error ?? `请求失败（${response.status}）`);
  }
  return normalizeCanonicalResponse<T>(path, await response.json());
}

function money(value: number | undefined): string {
  return `¥${(value ?? 0).toLocaleString('zh-CN')}`;
}
function tokens(value: number | null | undefined): string {
  return value == null ? '暂无数据' : `${value.toLocaleString('zh-CN')} Token`;
}
function dateTime(value: number | undefined): string {
  return value ? new Date(value).toLocaleString('zh-CN') : '—';
}
function compact(value: number): string {
  return value >= 100000000
    ? `${(value / 100000000).toFixed(2)} 亿`
    : value >= 10000
      ? `${(value / 10000).toFixed(1)} 万`
      : value.toLocaleString('zh-CN');
}

function Login({ onLoggedIn }: { onLoggedIn: (staff: Staff) => void }) {
  const [username, setUsername] = useState('admin');
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError('');
    try {
      const result = await api<{ staff: Staff }>('/api/v1/auth/login', {
        method: 'POST',
        body: JSON.stringify({ username, password }),
      });
      onLoggedIn(result.staff);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '登录失败');
    } finally {
      setBusy(false);
    }
  }
  return (
    <main className="login-shell">
      <section className="login-card">
        <div className="brand-mark large">
          <Gauge size={24} />
        </div>
        <p className="eyebrow">LONGXIN · SALES OPERATIONS</p>
        <h1>销售运营中心</h1>
        <p className="login-subtitle">管理客户系统、额度和交付状态。</p>
        <form onSubmit={submit} className="stack-form">
          <label>
            账号
            <input
              value={username}
              onChange={(event) => setUsername(event.target.value)}
              autoComplete="username"
            />
          </label>
          <label>
            密码
            <input
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              type="password"
              autoComplete="current-password"
            />
          </label>
          {error && <p className="form-error">{error}</p>}
          <button className="primary full" disabled={busy}>
            {busy ? '登录中…' : '登录'}
          </button>
        </form>
        <p className="login-note">
          新环境默认账号为 admin，生产环境请通过 SALES_ADMIN_PASSWORD 设置密码。
        </p>
      </section>
    </main>
  );
}

function Metric({
  label,
  value,
  detail,
  icon: Icon,
  accent = false,
}: {
  label: string;
  value: string;
  detail: string;
  icon: typeof Gauge;
  accent?: boolean;
}) {
  return (
    <article className={`metric ${accent ? 'metric-accent' : ''}`}>
      <div className="metric-head">
        <span>{label}</span>
        <Icon size={17} />
      </div>
      <strong>{value}</strong>
      <small>{detail}</small>
    </article>
  );
}

function Overview({ onNavigate }: { onNavigate: (view: View) => void }) {
  const [data, setData] = useState<Dashboard | null>(null);
  const [error, setError] = useState('');
  const load = () =>
    api<Dashboard>('/api/v1/dashboard')
      .then(setData)
      .catch((reason) => setError(reason instanceof Error ? reason.message : '读取失败'));
  useEffect(() => {
    void load();
  }, []);
  if (error)
    return (
      <Panel title="经营总览">
        <ErrorState message={error} onRetry={load} />
      </Panel>
    );
  if (!data) return <Loading />;
  const max = Math.max(...data.days.map((day) => day.amount), 1);
  return (
    <div className="page-content">
      <PageHeading
        eyebrow="LONGXIN · OPERATIONS"
        title="经营总览"
        subtitle="客户、额度与待办，一目了然。"
      >
        <button className="ghost-button" onClick={load}>
          <RefreshCw size={15} />
          刷新
        </button>
      </PageHeading>
      <div className="metric-grid">
        <Metric
          label="近 30 天充值签发"
          value={money(data.recharged30d)}
          detail={`累计签发 ${money(data.totalRecharged)}`}
          icon={CircleDollarSign}
          accent
        />
        <Metric
          label="客户系统"
          value={`${data.totalCustomers} 套`}
          detail={`${data.online} 套近期上报`}
          icon={MonitorCog}
        />
        <Metric
          label="待审核申请"
          value={`${data.pending} 笔`}
          detail="需要管理员确认"
          icon={ClipboardList}
        />
        <Metric
          label="未分配额度"
          value={compact(data.poolRemaining)}
          detail="Token · 按最新上报汇总"
          icon={Gauge}
        />
      </div>
      <div className="content-grid two-columns">
        <Panel title="充值趋势" subtitle="最近 30 天 · 按签发 / 批准时间">
          <div className="bar-chart">
            {data.days.map((day) => (
              <div
                className="bar-column"
                key={day.at}
                title={`${dateTime(day.at)}：${money(day.amount)}`}
              >
                <i
                  style={{ height: `${Math.max(4, (day.amount / max) * 100)}%` }}
                  className={day.amount ? '' : 'empty-bar'}
                />
              </div>
            ))}
          </div>
          <div className="chart-foot">
            <span>30 天前</span>
            <span>今天</span>
          </div>
        </Panel>
        <Panel title="需要关注" subtitle="优先处理今天的待办">
          <ActionRow
            icon={<ClipboardList size={17} />}
            title={`${data.pending} 笔充值申请待审核`}
            detail="确认金额后为客户分配额度"
            onClick={() => onNavigate('requests')}
          />
          <ActionRow
            icon={<AlertTriangle size={17} />}
            title={`${data.customers.filter((customer) => customer.stale).length} 套系统上报待确认`}
            detail="超过 65 分钟未上报或尚未连接"
            onClick={() => onNavigate('customers')}
          />
          <ActionRow
            icon={<Package size={17} />}
            title="检查发布与环境"
            detail="选择目标，分批升级测试与生产"
            onClick={() => onNavigate('upgrades')}
          />
        </Panel>
      </div>
      <div className="content-grid two-columns">
        <Panel title="客户额度分布" subtitle="未分配 Token · 最近快照">
          <div className="customer-list">
            {data.customers.map((customer) => (
              <div className="customer-row" key={customer.id}>
                <span className="avatar">{customer.name.slice(0, 1)}</span>
                <div className="customer-row-main">
                  <div>
                    <b>{customer.name}</b>
                    <span>{tokens(customer.lastReport?.poolTokens)}</span>
                  </div>
                  <div className="progress">
                    <i
                      style={{
                        width: `${Math.min(100, ((customer.lastReport?.poolTokens ?? 0) / Math.max(data.poolRemaining, 1)) * 100)}%`,
                      }}
                    />
                  </div>
                </div>
              </div>
            ))}
          </div>
        </Panel>
        <Panel title="最近动态" subtitle="充值与审核记录">
          <div className="activity-list">
            {data.activities.map((activity) => (
              <div className="activity" key={activity.id}>
                <span
                  className={`activity-dot ${activity.status === 'pending' ? 'pending' : ''}`}
                />
                <div>
                  <b>{activity.name}</b>
                  <span>
                    {activity.status === 'pending'
                      ? '提交充值申请'
                      : activity.status === 'approved'
                        ? '充值已批准'
                        : activity.status === 'issued'
                          ? '已签发离线充值码'
                          : '充值申请已拒绝'}{' '}
                    · {money(activity.amount)}
                  </span>
                </div>
                <time>{dateTime(activity.at)}</time>
              </div>
            ))}
          </div>
        </Panel>
      </div>
      <Panel title="系统运行概况" subtitle="按最近上报判断连接状态，不代表实时探测">
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>客户系统</th>
                <th>环境</th>
                <th>连接状态</th>
                <th>应用版本</th>
                <th>最近上报</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {data.customers.map((customer) => (
                <tr key={customer.id}>
                  <td>
                    <b>{customer.name}</b>
                    <small>{customer.ip || '尚未配置地址'}</small>
                  </td>
                  <td>
                    <Tag
                      text={
                        customer.environment === 'production'
                          ? '生产'
                          : customer.environment === 'test'
                            ? '测试'
                            : '待分类'
                      }
                    />
                  </td>
                  <td>
                    <Tag
                      good={customer.online}
                      text={
                        customer.online ? '近期上报' : customer.lastReport ? '上报过期' : '未接入'
                      }
                    />
                  </td>
                  <td>
                    {customer.lastReport?.appVersion
                      ? `v${customer.lastReport.appVersion}`
                      : '待上报'}
                  </td>
                  <td>{dateTime(customer.lastSeenAt ?? customer.lastReport?.reportedAt)}</td>
                  <td>
                    <button className="link-button" onClick={() => onNavigate('customers')}>
                      查看 <ChevronRight size={14} />
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Panel>
    </div>
  );
}

function Customers() {
  const [customers, setCustomers] = useState<Customer[]>([]);
  const [selected, setSelected] = useState<Customer | null>(null);
  const [name, setName] = useState('');
  const [systemId, setSystemId] = useState('');
  const [ip, setIp] = useState('');
  const [protocol, setProtocol] = useState<'http' | 'https'>('https');
  const [port, setPort] = useState('3443');
  const [environment, setEnvironment] = useState('unclassified');
  const [contact, setContact] = useState('');
  const [notes, setNotes] = useState('');
  const [error, setError] = useState('');
  const [token, setToken] = useState('');
  const [tokenKind, setTokenKind] = useState<'api' | 'code'>('api');
  const [amount, setAmount] = useState('1000');
  const [resetUsername, setResetUsername] = useState('');
  const [resetResult, setResetResult] = useState('');
  const [orders, setOrders] = useState<
    Array<{
      id: string;
      method: string;
      requestedAmount?: number | null;
      amount?: number;
      status: string;
      processedBy?: string;
      createdAt: number;
    }>
  >([]);
  const [usage, setUsage] = useState<
    Array<{ poolTokens: number; totalRecharged: number; appVersion: string; reportedAt: number }>
  >([]);
  const load = () =>
    api<Customer[]>('/api/v1/customers')
      .then(setCustomers)
      .catch((reason) => setError(reason instanceof Error ? reason.message : '读取失败'));
  const loadDetail = useCallback(async (customerId: string) => {
    try {
      const [nextOrders, nextUsage] = await Promise.all([
        api<typeof orders>(`/api/v1/customers/${customerId}/recharge-orders`),
        api<typeof usage>(`/api/v1/customers/${customerId}/usage-reports`),
      ]);
      setOrders(nextOrders);
      setUsage(nextUsage);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '客户详情读取失败');
    }
  }, []);
  useEffect(() => {
    void load();
  }, []);
  function selectCustomer(customer: Customer) {
    setSelected(customer);
    setToken('');
    setTokenKind('api');
    setResetResult('');
    void loadDetail(customer.id);
  }
  async function create() {
    try {
      const result = await api<{ customer: Customer; apiTokenOnce: string }>('/api/v1/customers', {
        method: 'POST',
        body: JSON.stringify({
          name,
          system_id: systemId,
          configured_ip: ip,
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
      setToken(result.apiTokenOnce ?? '');
      setTokenKind('api');
      void loadDetail(customer.id);
      setName('');
      setSystemId('');
      setIp('');
      setProtocol('https');
      setPort('3443');
      setEnvironment('unclassified');
      setContact('');
      setNotes('');
      setError('客户已创建，请立即复制 API Token 并交给客户系统配置');
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '创建失败');
    }
  }
  async function issueCode() {
    if (!selected) return;
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
      setError('充值码已生成，请安全交付客户');
      if (selected) void loadDetail(selected.id);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '生成失败');
    }
  }
  function updateSelected(patch: Partial<Customer>) {
    setSelected((current) => (current ? { ...current, ...patch } : current));
  }
  async function saveCustomer() {
    if (!selected) return;
    try {
      const result = await api<{ customer: Customer }>(`/api/v1/customers/${selected.id}`, {
        method: 'PATCH',
        body: JSON.stringify({
          name: selected.name,
          system_id: selected.systemId,
          environment: selected.environment,
          configured_ip: selected.ip,
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
      setError('客户档案已保存');
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '保存失败');
    }
  }
  async function revealToken() {
    if (!selected) return;
    try {
      const result = await api<{ apiTokenOnce: string }>(
        `/api/v1/customers/${selected.id}/api-token/rotate`,
        { method: 'POST', body: JSON.stringify({}) },
      );
      setToken(result.apiTokenOnce);
      setTokenKind('api');
      setError('API Token 已轮换并显示一次，请立即复制并交给客户系统配置');
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Token 读取失败');
    }
  }
  async function resetAdminPassword() {
    if (!selected || !resetUsername.trim()) return setError('请填写要重置的管理员用户名');
    try {
      const result = await api<{ username: string; newPassword: string }>(
        `/api/v1/customers/${selected.id}/reset-admin-password`,
        { method: 'POST', body: JSON.stringify({ username: resetUsername.trim() }) },
      );
      setResetResult(`用户名：${result.username}\n新密码：${result.newPassword}`);
      setError('远程密码已重置，请立即记录并转达客户');
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '远程重置失败');
    }
  }
  return (
    <div className="page-content">
      <PageHeading
        eyebrow="CUSTOMER FLEET"
        title="客户系统"
        subtitle="每一行是一套已交付的龙信 AI 助手系统；点开客户可管理充值码、订单和上报流水。最近来源 IP 用于核对客户实际出口地址。"
      >
        <button className="ghost-button" onClick={load}>
          <RefreshCw size={15} />
          刷新
        </button>
      </PageHeading>
      <div className="content-grid customer-layout">
        <Panel title="新建客户" subtitle="创建后 API Token 只在首次返回">
          <div className="stack-form">
            <input
              placeholder="客户名称"
              value={name}
              onChange={(event) => setName(event.target.value)}
            />
            <input
              placeholder="客户系统 ID（可稍后编辑）"
              value={systemId}
              onChange={(event) => setSystemId(event.target.value)}
            />
            <input
              placeholder="客户 IP（可选）"
              value={ip}
              onChange={(event) => setIp(event.target.value)}
            />
            <div className="detail-grid">
              <label>
                连接协议
                <select
                  value={protocol}
                  onChange={(event) => {
                    const next = event.target.value as 'http' | 'https';
                    setProtocol(next);
                    setPort(next === 'https' ? '3443' : '3000');
                  }}
                >
                  <option value="https">HTTPS（3443）</option>
                  <option value="http">HTTP（3000）</option>
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
            </div>
            <div className="detail-grid">
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
                联系方式
                <input value={contact} onChange={(event) => setContact(event.target.value)} />
              </label>
            </div>
            <textarea
              rows={2}
              placeholder="备注（可选，方便区分客户）"
              value={notes}
              onChange={(event) => setNotes(event.target.value)}
            />
            <button className="primary full" onClick={create}>
              <Users size={15} />
              创建
            </button>
          </div>
          {error && <p className="inline-note">{error}</p>}
        </Panel>
        <Panel title="客户列表" subtitle={`${customers.length} 套系统`}>
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>客户</th>
                  <th>备注</th>
                  <th>配置地址</th>
                  <th>最近来源 IP</th>
                  <th>最近上报</th>
                  <th>累计充值</th>
                  <th>档位</th>
                  <th>状态</th>
                </tr>
              </thead>
              <tbody>
                {customers.map((customer) => (
                  <tr
                    className={selected?.id === customer.id ? 'selected-row' : ''}
                    key={customer.id}
                    onClick={() => selectCustomer(customer)}
                  >
                    <td>
                      <button className="customer-link">{customer.name}</button>
                      <small>{customer.systemId || '未登记系统 ID'}</small>
                      <small>{customer.contact || '未填写联系方式'}</small>
                    </td>
                    <td>{customer.notes || '—'}</td>
                    <td>
                      {customer.ip ? `${customer.protocol}://${customer.ip}:${customer.port}` : '—'}
                      <small>
                        {customer.environment === 'production'
                          ? '生产'
                          : customer.environment === 'test'
                            ? '测试'
                            : '待分类'}
                      </small>
                    </td>
                    <td>
                      {customer.lastSeenIP || '—'}
                      {customer.ip &&
                        customer.lastSeenIP &&
                        customer.ip !== customer.lastSeenIP && <Tag text="与配置不一致" />}
                    </td>
                    <td>{dateTime(customer.lastReport?.reportedAt)}</td>
                    <td>{money(customer.totalRecharged)}</td>
                    <td>
                      {customer.tier
                        ? `${customer.tier.admins} 管理员 / ${customer.tier.accounts} 账号`
                        : '—'}
                    </td>
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
        </Panel>
      </div>
      {selected && (
        <>
          <Panel title={selected.name} subtitle="客户档案、连接信息与系统级额度">
            <div className="content-grid two-columns">
              <div className="stack-form">
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
                <div className="detail-grid">
                  <label>
                    协议
                    <select
                      value={selected.protocol}
                      onChange={(event) =>
                        updateSelected({
                          protocol: event.target.value as 'http' | 'https',
                          port: event.target.value === 'https' ? 3443 : 3000,
                        })
                      }
                    >
                      <option value="http">HTTP</option>
                      <option value="https">HTTPS</option>
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
                </div>
                <label>
                  配置 IP
                  <input
                    value={selected.ip}
                    placeholder="例如 192.168.33.109"
                    onChange={(event) => updateSelected({ ip: event.target.value })}
                  />
                </label>
                <div className="detail-grid">
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
                </div>
                <label>
                  联系方式
                  <input
                    value={selected.contact ?? ''}
                    onChange={(event) => updateSelected({ contact: event.target.value })}
                  />
                </label>
                <label>
                  备注
                  <textarea
                    rows={3}
                    value={selected.notes ?? ''}
                    onChange={(event) => updateSelected({ notes: event.target.value })}
                  />
                </label>
                <div className="detail-grid connection-facts">
                  <label>
                    最近实际来源 IP
                    <strong>{selected.lastSeenIP || '暂无上报记录'}</strong>
                  </label>
                  <label>
                    最近上报时间
                    <strong>
                      {dateTime(selected.lastSeenAt ?? selected.lastReport?.reportedAt)}
                    </strong>
                  </label>
                </div>
                {selected.ip && selected.lastSeenIP && selected.ip !== selected.lastSeenIP && (
                  <p className="form-error">
                    最近来源 IP 与配置 IP 不一致，请核实客户系统的实际出口地址。
                  </p>
                )}
                <button className="primary" onClick={saveCustomer}>
                  <Save size={15} />
                  保存客户档案
                </button>
              </div>
              <div className="detail-note">
                <ShieldCheck size={20} />
                <b>系统级额度与充值</b>
                <p>
                  充值码绑定系统 ID，并由客户系统本地验签。销售总台不会接收普通用户的个人充值信息。
                </p>
                <label>
                  充值金额（元）
                  <input
                    type="number"
                    min="1"
                    value={amount}
                    onChange={(event) => setAmount(event.target.value)}
                  />
                </label>
                <button className="primary" onClick={issueCode} disabled={!selected.systemId}>
                  <CircleDollarSign size={15} />
                  生成一次性充值码
                </button>
                {!selected.systemId && (
                  <p className="inline-note">请先登记客户系统 ID，才能签发充值码。</p>
                )}
              </div>
            </div>
          </Panel>
          <div className="content-grid two-columns">
            <Panel title="API 令牌" subtitle="客户在系统额度页配置总部地址时使用">
              <div className="action-row">
                <button className="secondary" onClick={revealToken}>
                  <KeyRound size={15} />
                  显示客户 Token
                </button>
                {token && tokenKind === 'api' && (
                  <div className="secret-box">
                    <span>{token}</span>
                    <button
                      className="icon-button"
                      title="复制"
                      onClick={() => navigator.clipboard.writeText(token)}
                    >
                      <Copy size={15} />
                    </button>
                  </div>
                )}
              </div>
            </Panel>
            <Panel title="远程支持" subtitle="客户 VM 能被销售中心访问时可用">
              <div className="stack-form">
                <label>
                  要重置的管理员用户名
                  <input
                    placeholder="例如 cnhh"
                    value={resetUsername}
                    onChange={(event) => setResetUsername(event.target.value)}
                  />
                </label>
                <button className="danger" onClick={resetAdminPassword} disabled={!selected.ip}>
                  <KeyRound size={15} />
                  重置客户管理员密码
                </button>
                {resetResult && <div className="secret-box">{resetResult}</div>}
                {!selected.ip && <p className="inline-note">该客户还没有配置 IP，无法远程连接。</p>}
              </div>
            </Panel>
          </div>
          <div className="content-grid two-columns">
            <Panel title="充值订单历史" subtitle="在线申请与离线签发的统一时间线">
              {orders.length ? (
                <div className="table-wrap">
                  <table>
                    <thead>
                      <tr>
                        <th>时间</th>
                        <th>方式</th>
                        <th>申请金额</th>
                        <th>批准金额</th>
                        <th>状态</th>
                        <th>经手人</th>
                      </tr>
                    </thead>
                    <tbody>
                      {orders.slice(0, 12).map((order) => (
                        <tr key={order.id}>
                          <td>{dateTime(order.createdAt)}</td>
                          <td>{order.method === 'online' ? '在线申请' : '充值码'}</td>
                          <td>{order.requestedAmount ? money(order.requestedAmount) : '—'}</td>
                          <td>{order.amount ? money(order.amount) : '—'}</td>
                          <td>
                            <Tag
                              good={order.status === 'approved' || order.status === 'issued'}
                              text={order.status}
                            />
                          </td>
                          <td>{order.processedBy ?? '—'}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              ) : (
                <EmptyState
                  icon={<ClipboardList size={20} />}
                  title="暂无充值记录"
                  detail="该客户的订单会在申请或签发后出现在这里。"
                />
              )}
            </Panel>
            <Panel title="消费与上报历史" subtitle="最近 10 次客户系统汇总快照">
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
                  icon={<BarChart3 size={20} />}
                  title="暂无上报数据"
                  detail="客户系统完成定时同步后会显示余额和版本。"
                />
              )}
            </Panel>
          </div>
          {token && tokenKind === 'code' && (
            <Panel title="一次性充值码" subtitle="仅展示当前刚签发的充值码，请通过安全渠道交付客户">
              <div className="secret-box">
                <span>{token}</span>
                <button
                  className="icon-button"
                  title="复制"
                  onClick={() => navigator.clipboard.writeText(token)}
                >
                  <Copy size={15} />
                </button>
              </div>
            </Panel>
          )}
        </>
      )}
    </div>
  );
}

function Requests() {
  const [orders, setOrders] = useState<
    Array<{
      id: string;
      customerName: string;
      requestedAmount?: number | null;
      note?: string;
      createdAt: number;
      status: string;
    }>
  >([]);
  const [error, setError] = useState('');
  const load = useCallback(
    () =>
      api<typeof orders>('/api/v1/recharge-requests?status=pending')
        .then(setOrders)
        .catch((reason) => setError(reason instanceof Error ? reason.message : '读取失败')),
    [],
  );
  useEffect(() => {
    void load();
  }, [load]);
  async function process(
    id: string,
    action: 'approve' | 'reject',
    requestedAmount?: number | null,
  ) {
    const amount =
      action === 'approve'
        ? Number(window.prompt('确认充值金额（元）', String(requestedAmount ?? '')))
        : undefined;
    if (action === 'approve' && (!amount || amount <= 0)) return;
    try {
      await api(`/api/v1/recharge-requests/${id}/${action}`, {
        method: 'POST',
        body: JSON.stringify(action === 'approve' ? { amount: Number(amount).toFixed(2) } : {}),
      });
      load();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '操作失败');
    }
  }
  return (
    <div className="page-content">
      <PageHeading
        eyebrow="REVENUE OPERATIONS"
        title="充值审核"
        subtitle="客户系统在线提交的充值申请，审批后会自动生成充值码。"
      >
        <button className="ghost-button" onClick={load}>
          <RefreshCw size={15} />
          刷新
        </button>
      </PageHeading>
      <Panel title="待处理申请" subtitle="批准前请先核实到账或转账凭证">
        {error && <p className="form-error">{error}</p>}
        {orders.length ? (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>客户</th>
                  <th>申请金额</th>
                  <th>备注</th>
                  <th>提交时间</th>
                  <th>操作</th>
                </tr>
              </thead>
              <tbody>
                {orders.map((order) => (
                  <tr key={order.id}>
                    <td>
                      <b>{order.customerName}</b>
                    </td>
                    <td>{order.requestedAmount ? money(order.requestedAmount) : '未填写'}</td>
                    <td>{order.note || '—'}</td>
                    <td>{dateTime(order.createdAt)}</td>
                    <td className="actions">
                      <button
                        className="primary small"
                        onClick={() => process(order.id, 'approve', order.requestedAmount)}
                      >
                        <Check size={14} />
                        批准
                      </button>
                      <button className="danger small" onClick={() => process(order.id, 'reject')}>
                        <X size={14} />
                        拒绝
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <EmptyState
            icon={<ClipboardList size={20} />}
            title="暂无待审核申请"
            detail="客户系统提交在线充值后会出现在这里。"
          />
        )}
      </Panel>
    </div>
  );
}

function Reconciliation() {
  const [rows, setRows] = useState<
    Array<{
      name: string;
      rechargedAmount: number;
      rechargedTokens: number;
      consumedTokens: number | null;
    }>
  >([]);
  useEffect(() => {
    api<typeof rows>('/api/v1/reconciliation')
      .then(setRows)
      .catch(() => undefined);
  }, []);
  return (
    <div className="page-content">
      <PageHeading
        eyebrow="FINANCE & USAGE"
        title="对账中心"
        subtitle="对比充值记录和客户系统上报的实际消耗。"
      />
      <Panel title="系统对账" subtitle="没有足够上报数据的客户会明确显示暂无数据">
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>客户</th>
                <th>充值金额</th>
                <th>充值 Token</th>
                <th>已记录消耗 Token</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <tr key={row.name}>
                  <td>
                    <b>{row.name}</b>
                  </td>
                  <td>{money(row.rechargedAmount)}</td>
                  <td>{tokens(row.rechargedTokens)}</td>
                  <td>{tokens(row.consumedTokens)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Panel>
    </div>
  );
}
function Alerts() {
  const [rows, setRows] = useState<
    Array<{ name: string; poolTokens: number; daysLeft: number | null }>
  >([]);
  useEffect(() => {
    api<typeof rows>('/api/v1/alerts')
      .then(setRows)
      .catch(() => undefined);
  }, []);
  return (
    <div className="page-content">
      <PageHeading
        eyebrow="CUSTOMER SUCCESS"
        title="余额预警"
        subtitle="按最近上报估算消耗速度，预计 14 天内耗尽的客户会出现在这里。"
      />
      <Panel title="需要主动联系的客户">
        {rows.length ? (
          <div className="alert-grid">
            {rows.map((row) => (
              <article className="alert-card" key={row.name}>
                <div className="alert-icon">
                  <AlertTriangle size={18} />
                </div>
                <div>
                  <b>{row.name}</b>
                  <p>
                    {tokens(row.poolTokens)} · 预计还能使用{' '}
                    <strong>{row.daysLeft == null ? '即将耗尽' : `${row.daysLeft} 天`}</strong>
                  </p>
                </div>
              </article>
            ))}
          </div>
        ) : (
          <EmptyState
            icon={<ShieldCheck size={20} />}
            title="暂无余额预警"
            detail="需要至少两次有效的累计消耗上报才能估算。"
          />
        )}
      </Panel>
    </div>
  );
}

function Upgrades() {
  const [releases, setReleases] = useState<{ app: ReleaseMeta; opencode: ReleaseMeta }>({
    app: null,
    opencode: null,
  });
  const [customers, setCustomers] = useState<Customer[]>([]);
  const [files, setFiles] = useState<Record<string, File | null>>({ app: null, opencode: null });
  const [versions, setVersions] = useState<Record<string, string>>({ app: '', opencode: '' });
  const [selected, setSelected] = useState<string[]>([]);
  const [message, setMessage] = useState('');
  const [upgradeVersion, setUpgradeVersion] = useState('');
  const [upgradeResults, setUpgradeResults] = useState<
    Array<{ id?: string; name: string; ok: boolean; state?: string; error?: string }>
  >([]);
  const load = useCallback(
    () =>
      Promise.all([api<typeof releases>('/api/v1/releases'), api<Customer[]>('/api/v1/customers')])
        .then(([nextReleases, nextCustomers]) => {
          setReleases(nextReleases);
          setCustomers(nextCustomers);
        })
        .catch((reason) => setMessage(reason instanceof Error ? reason.message : '读取失败')),
    [],
  );
  useEffect(() => {
    void load();
  }, [load]);
  async function uploadRelease(type: ReleaseType) {
    const file = files[type];
    if (!file || !versions[type]) return setMessage('请选择升级包并填写版本号');
    const form = new FormData();
    form.append('file', file);
    try {
      await api(
        `/api/v1/releases/${canonicalReleaseType(type)}?version=${encodeURIComponent(versions[type])}`,
        {
          method: 'POST',
          body: form,
        },
      );
      setMessage(`${releaseLabels[type]}上传并校验成功`);
      load();
    } catch (reason) {
      setMessage(reason instanceof Error ? reason.message : '上传失败');
    }
  }
  async function upgrade(type: ReleaseType) {
    if (!selected.length) return setMessage('请先选择升级目标');
    setUpgradeResults([]);
    setUpgradeVersion('');
    try {
      const result = await api<{
        version: string;
        results: Array<{ id?: string; name: string; ok: boolean; state?: string; error?: string }>;
      }>(`/api/v1/upgrade-all/${canonicalReleaseType(type)}`, {
        method: 'POST',
        body: JSON.stringify({ customer_ids: selected }),
      });
      setUpgradeVersion(result.version);
      setUpgradeResults(result.results);
      setMessage(
        `升级任务已完成：${result.version}，成功 ${result.results.filter((item) => item.ok).length}/${result.results.length}`,
      );
    } catch (reason) {
      setMessage(reason instanceof Error ? reason.message : '升级失败');
    }
  }
  const targetCustomers = customers.filter(
    (customer) => customer.status === 'active' && customer.ip,
  );
  return (
    <div className="page-content">
      <PageHeading
        eyebrow="RELEASE MANAGEMENT"
        title="升级管理"
        subtitle="上传后选择客户系统，客户端会备份、校验、升级并在失败时回滚；未配置 IP 的离线客户仍需在客户自己的升级页手动上传。"
      />
      <Panel title="选择升级目标" subtitle="建议先验证测试环境，再发布生产">
        {targetCustomers.length ? (
          <div className="target-grid">
            {targetCustomers.map((customer) => (
              <label className="target-item" key={customer.id}>
                <input
                  type="checkbox"
                  checked={selected.includes(customer.id)}
                  onChange={(event) =>
                    setSelected((current) =>
                      event.target.checked
                        ? [...current, customer.id]
                        : current.filter((id) => id !== customer.id),
                    )
                  }
                />
                <span>
                  <b>{customer.name}</b>
                  <small>
                    {customer.ip} ·{' '}
                    {customer.environment === 'production'
                      ? '生产'
                      : customer.environment === 'test'
                        ? '测试'
                        : '待分类'}
                  </small>
                </span>
              </label>
            ))}
          </div>
        ) : (
          <EmptyState
            icon={<MonitorCog size={20} />}
            title="暂无可升级客户"
            detail="请先创建客户并配置 IP，状态正常的客户才会出现在升级目标中。"
          />
        )}
      </Panel>
      <div className="content-grid two-columns">
        {releaseTypes.map((type) => (
          <Panel
            key={type}
            title={releaseLabels[type]}
            subtitle={
              releases[type]
                ? `当前 v${releases[type]!.version} · ${dateTime(releases[type]!.uploadedAt)}`
                : '尚未上传'
            }
          >
            <label className="file-drop">
              <input
                type="file"
                accept=".tar.gz"
                onChange={(event) =>
                  setFiles((current) => ({ ...current, [type]: event.target.files?.[0] ?? null }))
                }
              />
              <Package size={24} />
              <strong>点击选择 `.tar.gz` 升级包</strong>
              <span>{files[type]?.name ?? '点击选择文件'}</span>
              <small>上传前会校验 manifest、版本号和必要文件</small>
            </label>
            <div className="inline-form">
              <input
                placeholder="版本号，例如 3.0.3"
                value={versions[type]}
                onChange={(event) =>
                  setVersions((current) => ({ ...current, [type]: event.target.value }))
                }
              />
              <button className="secondary" onClick={() => uploadRelease(type)}>
                <Package size={15} />
                上传校验
              </button>
              <button className="danger" onClick={() => upgrade(type)}>
                <ArrowUpRight size={15} />
                升级所选
              </button>
            </div>
          </Panel>
        ))}
      </div>
      {upgradeResults.length > 0 && (
        <Panel
          title="升级结果"
          subtitle={`版本 v${upgradeVersion} · 共 ${upgradeResults.length} 套客户系统`}
        >
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>客户系统</th>
                  <th>结果</th>
                  <th>说明</th>
                </tr>
              </thead>
              <tbody>
                {upgradeResults.map((result) => (
                  <tr key={result.id ?? result.name}>
                    <td>
                      <b>{result.name}</b>
                    </td>
                    <td>
                      <Tag good={result.ok} text={result.ok ? '升级成功' : '待确认'} />
                    </td>
                    <td>
                      {result.error ??
                        (result.state === 'unverified'
                          ? '客户端已响应，但版本尚未确认'
                          : '已完成版本确认')}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Panel>
      )}
      {message && <p className="inline-note">{message}</p>}
    </div>
  );
}

function PublicKey() {
  const [key, setKey] = useState('');
  useEffect(() => {
    api<{ publicKeyPem: string }>('/api/v1/public-key')
      .then((result) => setKey(result.publicKeyPem))
      .catch(() => undefined);
  }, []);
  return (
    <div className="page-content">
      <PageHeading
        eyebrow="TRUST CONFIGURATION"
        title="总部公钥"
        subtitle="客户系统使用它在本地验证销售总台签发的充值码。"
      />
      <Panel title="Sales Hub Ed25519 公钥" subtitle="公钥可以分发，不属于机密信息">
        <div className="key-box">
          <textarea value={key} readOnly rows={8} />
          <button className="secondary" onClick={() => navigator.clipboard.writeText(key)}>
            <Copy size={15} />
            复制公钥
          </button>
        </div>
      </Panel>
    </div>
  );
}

function Settings() {
  const [rate, setRate] = useState('41841');
  const [currentPassword, setCurrentPassword] = useState('');
  const [newPassword, setNewPassword] = useState('');
  const [message, setMessage] = useState('');
  const [audit, setAudit] = useState<
    Array<{ at: number; actor: string; method: string; path: string }>
  >([]);
  useEffect(() => {
    api<{ tokenExchangeRate: number }>('/api/v1/settings')
      .then((result) => setRate(String(result.tokenExchangeRate)))
      .catch(() => undefined);
  }, []);
  async function saveRate() {
    try {
      await api('/api/v1/settings', {
        method: 'PATCH',
        body: JSON.stringify({ token_exchange_rate: Number(rate) }),
      });
      setMessage('汇率已保存');
    } catch (reason) {
      setMessage(reason instanceof Error ? reason.message : '保存失败');
    }
  }
  async function savePassword() {
    try {
      await api('/api/v1/staff/password', {
        method: 'PATCH',
        body: JSON.stringify({ currentPassword, newPassword }),
      });
      setMessage('密码已修改');
      setCurrentPassword('');
      setNewPassword('');
    } catch (reason) {
      setMessage(reason instanceof Error ? reason.message : '修改失败');
    }
  }
  async function loadAudit() {
    try {
      setAudit(
        await api<Array<{ at: number; actor: string; method: string; path: string }>>(
          '/api/v1/audit/events',
        ),
      );
    } catch (reason) {
      setMessage(reason instanceof Error ? reason.message : '审计记录读取失败');
    }
  }
  return (
    <div className="page-content">
      <PageHeading
        eyebrow="SYSTEM GOVERNANCE"
        title="系统设置"
        subtitle="管理汇率、安全和操作记录。"
      />
      {message && <p className="inline-note">{message}</p>}
      <div className="content-grid two-columns">
        <Panel title="Token 兑换汇率" subtitle="只影响后续生成或批准的充值码">
          <div className="rate-editor">
            <label className="rate-field">
              <span>1 元人民币 =</span>
              <input
                className="short-input"
                type="number"
                min="1"
                value={rate}
                onChange={(event) => setRate(event.target.value)}
              />
              <span>Token</span>
            </label>
            <button className="primary rate-save" onClick={saveRate}>
              <Save size={15} />
              保存汇率
            </button>
          </div>
        </Panel>
        <Panel title="修改登录密码" subtitle="至少 8 位，需包含字母和数字">
          <div className="stack-form">
            <input
              type="password"
              placeholder="当前密码"
              value={currentPassword}
              onChange={(event) => setCurrentPassword(event.target.value)}
            />
            <input
              type="password"
              placeholder="新密码"
              value={newPassword}
              onChange={(event) => setNewPassword(event.target.value)}
            />
            <button className="primary" onClick={savePassword}>
              <KeyRound size={15} />
              修改密码
            </button>
          </div>
        </Panel>
      </div>
      <Panel title="审计原则" subtitle="所有敏感管理操作都应留下可追溯记录">
        <div className="audit-summary">
          <ShieldCheck size={20} />
          <span>
            新后端会记录操作者、接口、时间、来源 IP 和结果；不会记录密码、Token 明文或请求正文。
          </span>
        </div>
      </Panel>
      <Panel title="操作记录" subtitle="查看最近 200 条管理操作；不记录密码、Token 明文或请求正文">
        <div className="audit-summary">
          <span>
            <ShieldCheck size={20} /> 已记录操作者、接口、时间和来源 IP
          </span>
          <button className="secondary" onClick={loadAudit}>
            <ClipboardList size={15} />
            读取最近记录
          </button>
        </div>
        {audit.length ? (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>时间</th>
                  <th>操作者</th>
                  <th>操作</th>
                </tr>
              </thead>
              <tbody>
                {audit.map((record, index) => (
                  <tr key={`${record.at}-${index}`}>
                    <td>{dateTime(record.at)}</td>
                    <td>{record.actor}</td>
                    <td>
                      {record.method} {record.path}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <p className="inline-note">点击“读取最近记录”查看审计流水。</p>
        )}
      </Panel>
    </div>
  );
}

function PageHeading({
  eyebrow,
  title,
  subtitle,
  children,
}: {
  eyebrow: string;
  title: string;
  subtitle: string;
  children?: ReactNode;
}) {
  return (
    <header className="page-heading">
      <div>
        <p className="eyebrow">{eyebrow}</p>
        <h1>{title}</h1>
        <p>{subtitle}</p>
      </div>
      <div className="heading-actions">{children}</div>
    </header>
  );
}
function Panel({
  title,
  subtitle,
  children,
}: {
  title: string;
  subtitle?: string;
  children: ReactNode;
}) {
  return (
    <section className="panel">
      <header>
        <div>
          <h2>{title}</h2>
          {subtitle && <p>{subtitle}</p>}
        </div>
      </header>
      {children}
    </section>
  );
}
function ActionRow({
  icon,
  title,
  detail,
  onClick,
}: {
  icon: ReactNode;
  title: string;
  detail: string;
  onClick: () => void;
}) {
  return (
    <button className="action-row" onClick={onClick}>
      <span className="action-icon">{icon}</span>
      <span>
        <b>{title}</b>
        <small>{detail}</small>
      </span>
      <ChevronRight size={16} />
    </button>
  );
}
function Tag({ text, good = false }: { text: string; good?: boolean }) {
  return (
    <span className={`tag ${good ? 'tag-good' : ''}`}>
      {good && <i />}
      {text}
    </span>
  );
}
function Loading() {
  return (
    <div className="loading-state">
      <div className="spinner" />
      正在读取运营数据…
    </div>
  );
}
function EmptyState({ icon, title, detail }: { icon: ReactNode; title: string; detail: string }) {
  return (
    <div className="empty-state">
      <span>{icon}</span>
      <b>{title}</b>
      <p>{detail}</p>
    </div>
  );
}
function ErrorState({ message, onRetry }: { message: string; onRetry: () => void }) {
  return (
    <div className="empty-state">
      <span>
        <AlertTriangle size={20} />
      </span>
      <b>读取失败</b>
      <p>{message}</p>
      <button className="secondary" onClick={onRetry}>
        重新加载
      </button>
    </div>
  );
}

export default function App() {
  const [staff, setStaff] = useState<Staff | null>(null);
  const [view, setView] = useState<View>('overview');
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
        <div className="sidebar-label">工作台</div>
        <nav className="nav-list">
          {nav.map(({ id, label, icon: Icon, hint }) => (
            <button
              className={`nav-item ${view === id ? 'active' : ''}`}
              key={id}
              onClick={() => setView(id)}
            >
              <Icon size={17} />
              <span className="nav-copy">
                <strong>{label}</strong>
                <small>{hint}</small>
              </span>
              {view === id && <i />}
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
          <span className="secure-badge">
            <ShieldCheck size={14} /> 安全连接
          </span>
        </div>
        {view === 'overview' && <Overview onNavigate={setView} />}
        {view === 'customers' && <Customers />}
        {view === 'requests' && <Requests />}
        {view === 'reconciliation' && <Reconciliation />}
        {view === 'alerts' && <Alerts />}
        {view === 'upgrades' && <Upgrades />}
        {view === 'publickey' && <PublicKey />}
        {view === 'settings' && <Settings />}
      </main>
    </div>
  );
}
