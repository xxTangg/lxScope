import { useCallback, useEffect, useState } from 'react';
import {
  AlertTriangle,
  ChevronDown,
  ChevronRight,
  CircleDollarSign,
  ClipboardList,
  Gauge,
  MonitorCog,
  RefreshCw,
} from 'lucide-react';
import { api } from '../../api/client';
import { compact, dateTime, money, tokens } from '../../lib/format';
import type { Dashboard, FinanceSection, UpgradeSummary, View } from '../../types';
import {
  ActionRow,
  EmptyState,
  ErrorState,
  Loading,
  PageHeading,
  Panel,
  Tag,
} from '../../components/ui';

export function Metric({
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

export function Overview({
  onNavigate,
  refreshToken,
  upgradeSummary,
}: {
  onNavigate: (view: View, section?: FinanceSection) => void;
  refreshToken: number;
  upgradeSummary: UpgradeSummary | null;
}) {
  const [data, setData] = useState<Dashboard | null>(null);
  const [balanceAlerts, setBalanceAlerts] = useState<Array<{
    name: string;
    poolTokens: number;
    daysLeft: number | null;
  }> | null>(null);
  const [consumedTokens, setConsumedTokens] = useState<number | null>(null);
  const [consumptionUnavailable, setConsumptionUnavailable] = useState(false);
  const [error, setError] = useState('');
  const [showAllActivities, setShowAllActivities] = useState(false);
  const load = useCallback(() => {
    setError('');
    return Promise.all([
      api<Dashboard>('/api/v1/dashboard'),
      api<Array<{ name: string; poolTokens: number; daysLeft: number | null }>>(
        '/api/v1/alerts',
      ).catch(() => null),
      api<Array<{ consumedTokens: number | null }>>('/api/v1/reconciliation').catch(() => null),
    ])
      .then(([dashboard, alerts, reconciliation]) => {
        setData(dashboard);
        setBalanceAlerts(alerts);
        setConsumptionUnavailable(reconciliation === null);
        const measuredConsumption =
          reconciliation?.flatMap((row) =>
            typeof row.consumedTokens === 'number' ? [row.consumedTokens] : [],
          ) ?? [];
        setConsumedTokens(
          measuredConsumption.length
            ? measuredConsumption.reduce((sum, amount) => sum + amount, 0)
            : null,
        );
      })
      .catch((reason) => setError(reason instanceof Error ? reason.message : '读取失败'));
  }, []);
  useEffect(() => {
    const initialLoad = window.setTimeout(() => void load(), 0);
    const timer = window.setInterval(() => void load(), 30_000);
    return () => {
      window.clearTimeout(initialLoad);
      window.clearInterval(timer);
    };
  }, [load, refreshToken]);
  if (error)
    return (
      <Panel title="经营总览">
        <ErrorState message={error} onRetry={load} />
      </Panel>
    );
  if (!data) return <Loading />;
  const max = Math.max(...data.days.map((day) => day.amount), 1);
  const todayAmount = data.days.at(-1)?.amount ?? 0;
  const visibleActivities = showAllActivities ? data.activities : data.activities.slice(0, 5);
  const staleCustomers = data.customers.filter((customer) => customer.stale).length;
  const failedUpgrades = upgradeSummary?.failed ?? 0;
  return (
    <div className="page-content">
      <PageHeading
        eyebrow="SALES OPERATIONS"
        title="工作总览"
        subtitle="客户、充值、用量和需要处理的事项。"
      >
        <button className="ghost-button" onClick={load}>
          <RefreshCw size={15} />
          刷新
        </button>
      </PageHeading>
      <div className="metric-grid">
        <Metric
          label="客户系统"
          value={`${data.totalCustomers} 套`}
          detail={`${data.online} 套近期上报`}
          icon={MonitorCog}
        />
        <Metric
          label="近 30 天充值"
          value={money(data.recharged30d)}
          detail={`累计签发 ${money(data.totalRecharged)}`}
          icon={CircleDollarSign}
          accent
        />
        <Metric
          label="Token 消耗"
          value={consumedTokens === null ? '—' : compact(consumedTokens)}
          detail={
            consumedTokens === null
              ? consumptionUnavailable
                ? '对账数据加载失败'
                : '暂无有效上报数据'
              : '按客户系统上报估算累计消耗'
          }
          icon={Gauge}
        />
        <Metric
          label="待处理申请"
          value={`${data.pending} 笔`}
          detail="需要管理员确认"
          icon={ClipboardList}
        />
      </div>
      <div className="content-grid two-columns overview-action-grid">
        <Panel title="待办事项" subtitle="高频操作集中在这里">
          <ActionRow
            icon={<ClipboardList size={17} />}
            title={`${data.pending} 笔充值申请待审核`}
            detail="确认金额后为客户分配额度"
            onClick={() => onNavigate('finance', 'requests')}
          />
          <ActionRow
            icon={<AlertTriangle size={17} />}
            title={`${staleCustomers} 套系统上报待确认`}
            detail="超过 65 分钟未上报或尚未连接"
            onClick={() => onNavigate('customers')}
          />
          <ActionRow
            icon={<Gauge size={17} />}
            title={`${balanceAlerts?.length ?? '—'} 项余额预警`}
            detail={
              balanceAlerts === null ? '查看余额预警和续费关注客户' : '预计 14 天内耗尽的客户'
            }
            onClick={() => onNavigate('finance', 'alerts')}
          />
        </Panel>
        <Panel title="重点异常" subtitle="余额风险与最近一次升级结果">
          {balanceAlerts === null ? (
            <div className="exception-line">
              <span className="exception-dot neutral" />
              <div>
                <b>余额预警暂不可用</b>
                <small>进入财务页重新检查</small>
              </div>
              <button className="link-button" onClick={() => onNavigate('finance', 'alerts')}>
                查看
              </button>
            </div>
          ) : balanceAlerts.length ? (
            balanceAlerts.slice(0, 3).map((item) => (
              <div className="exception-line" key={item.name}>
                <span className="exception-dot warning" />
                <div>
                  <b>{item.name}</b>
                  <small>
                    {tokens(item.poolTokens)} ·{' '}
                    {item.daysLeft == null ? '即将耗尽' : `约 ${item.daysLeft} 天`}
                  </small>
                </div>
                <button className="link-button" onClick={() => onNavigate('finance', 'alerts')}>
                  详情
                </button>
              </div>
            ))
          ) : (
            <div className="exception-line">
              <span className="exception-dot good" />
              <div>
                <b>暂无余额预警</b>
                <small>当前客户余额状态正常</small>
              </div>
            </div>
          )}
          {failedUpgrades > 0 ? (
            <div className="exception-line">
              <span className="exception-dot danger" />
              <div>
                <b>{failedUpgrades} 个升级目标需要处理</b>
                <small>版本 {upgradeSummary?.version} · 最近执行</small>
              </div>
              <button className="link-button" onClick={() => onNavigate('upgrades')}>
                查看
              </button>
            </div>
          ) : (
            <div className="exception-line">
              <span className="exception-dot neutral" />
              <div>
                <b>升级执行记录</b>
                <small>
                  {upgradeSummary
                    ? `${upgradeSummary.total} 个目标 · 最近一次已完成`
                    : '执行后可查看状态和失败原因'}
                </small>
              </div>
              <button className="link-button" onClick={() => onNavigate('upgrades')}>
                进入
              </button>
            </div>
          )}
        </Panel>
      </div>

      <details className="dashboard-details">
        <summary>
          <span>运营趋势与系统状态</span>
          <small>充值趋势、客户额度、动态和连接状态</small>
          <ChevronDown size={16} aria-hidden="true" />
        </summary>
        <div className="content-grid two-columns dashboard-detail-grid">
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
                    className={day.amount > 0 ? '' : 'empty-bar'}
                  />
                </div>
              ))}
            </div>
            <div className="chart-foot">
              <span>30 天前</span>
              <span>今天 · {money(todayAmount)}</span>
            </div>
          </Panel>
          <Panel title="客户额度分布" subtitle="未分配 Token · 最近快照">
            {data.customers.length ? (
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
            ) : (
              <EmptyState
                icon={<Gauge size={18} />}
                title="暂无客户额度"
                detail="客户系统完成首次上报后显示额度快照。"
              />
            )}
          </Panel>
          <Panel title="最近充值动态" subtitle="在线申请与离线签发">
            {data.activities.length ? (
              <>
                <div className="activity-list" id="recent-activities">
                  {visibleActivities.map((activity) => (
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
                {data.activities.length > 5 && (
                  <button
                    className="link-button activity-toggle"
                    type="button"
                    aria-expanded={showAllActivities}
                    aria-controls="recent-activities"
                    onClick={() => setShowAllActivities((expanded) => !expanded)}
                  >
                    {showAllActivities ? '收起' : `展开全部 ${data.activities.length} 条`}
                    <ChevronDown size={14} />
                  </button>
                )}
              </>
            ) : (
              <EmptyState
                icon={<ClipboardList size={18} />}
                title="暂无近期动态"
                detail="新的充值申请和签发记录会显示在这里。"
              />
            )}
          </Panel>
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
                            customer.online
                              ? '近期上报'
                              : customer.lastReport
                                ? '上报过期'
                                : '未接入'
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
      </details>
    </div>
  );
}
