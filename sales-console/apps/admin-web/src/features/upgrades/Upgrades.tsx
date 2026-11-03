import { useCallback, useEffect, useMemo, useState } from 'react';
import { ArrowUpRight, ChevronDown, MonitorCog, Package, RefreshCw } from 'lucide-react';
import { api, canonicalReleaseType } from '../../api/client';
import { dateTime } from '../../lib/format';
import type { Customer, ReleaseMeta, ReleaseType, UpgradeSummary } from '../../types';
import { EmptyState, PageHeading, Panel, Tag } from '../../components/ui';

const releaseLabels: Record<ReleaseType, string> = {
  app: '龙信业务应用',
  core: 'AgentScope 平台',
};
const releaseTypes: ReleaseType[] = ['app', 'core'];
type UpgradeResult = { id?: string; name: string; ok: boolean; state?: string; error?: string };

export function Upgrades({
  refreshToken,
  onSummaryChange,
}: {
  refreshToken: number;
  onSummaryChange?: (summary: UpgradeSummary) => void;
}) {
  const [releases, setReleases] = useState<{ app: ReleaseMeta; core: ReleaseMeta }>({
    app: null,
    core: null,
  });
  const [customers, setCustomers] = useState<Customer[]>([]);
  const [files, setFiles] = useState<Record<string, File | null>>({ app: null, core: null });
  const [versions, setVersions] = useState<Record<string, string>>({ app: '', core: '' });
  const [selected, setSelected] = useState<string[]>([]);
  const [targetEnvironment, setTargetEnvironment] = useState('all');
  const [message, setMessage] = useState('');
  const [messageKind, setMessageKind] = useState<'success' | 'error' | 'info'>('info');
  const [upgradeVersion, setUpgradeVersion] = useState('');
  const [upgradeType, setUpgradeType] = useState<ReleaseType>('app');
  const [upgradeResults, setUpgradeResults] = useState<UpgradeResult[]>([]);
  const [isUpgrading, setIsUpgrading] = useState(false);

  const load = useCallback(
    () =>
      Promise.all([api<typeof releases>('/api/v1/releases'), api<Customer[]>('/api/v1/customers')])
        .then(([nextReleases, nextCustomers]) => {
          setReleases(nextReleases);
          setCustomers(nextCustomers);
        })
        .catch((reason) => {
          setMessage(reason instanceof Error ? reason.message : '读取升级信息失败');
          setMessageKind('error');
        }),
    [],
  );

  useEffect(() => {
    void load();
  }, [load, refreshToken]);

  async function uploadRelease(type: ReleaseType) {
    const file = files[type];
    if (!file || !versions[type]) {
      setMessage('请选择升级包并填写版本号');
      setMessageKind('error');
      return;
    }
    const form = new FormData();
    form.append('file', file);
    try {
      await api(
        `/api/v1/releases/${canonicalReleaseType(type)}?version=${encodeURIComponent(versions[type])}`,
        { method: 'POST', body: form },
      );
      setMessage(`${releaseLabels[type]}升级包已上传并校验成功`);
      setMessageKind('success');
      await load();
    } catch (reason) {
      setMessage(reason instanceof Error ? reason.message : '升级包上传失败');
      setMessageKind('error');
    }
  }

  async function upgrade(type: ReleaseType, customerIDs = selected) {
    if (!customerIDs.length) {
      setMessage('请先选择升级目标');
      setMessageKind('error');
      return;
    }
    const version = releases[type]?.version;
    if (!version) {
      setMessage(`请先发布${releaseLabels[type]}升级包`);
      setMessageKind('error');
      return;
    }
    if (
      !window.confirm(
        `将 ${releaseLabels[type]} v${version} 推送到 ${customerIDs.length} 套客户系统。确认开始？`,
      )
    )
      return;
    setIsUpgrading(true);
    setUpgradeResults([]);
    setUpgradeType(type);
    setUpgradeVersion(version);
    setMessage(`正在向 ${customerIDs.length} 套客户系统发送升级任务…`);
    setMessageKind('info');
    try {
      const result = await api<{ version: string; results: UpgradeResult[] }>(
        `/api/v1/upgrade-all/${canonicalReleaseType(type)}`,
        { method: 'POST', body: JSON.stringify({ customer_ids: customerIDs }) },
      );
      setUpgradeVersion(result.version);
      setUpgradeResults(result.results);
      const failed = result.results.filter((item) => !item.ok).length;
      setMessage(
        `本次执行完成：${result.version}，成功 ${result.results.length - failed}/${result.results.length}${failed ? `，${failed} 个目标需处理` : ''}`,
      );
      setMessageKind(failed ? 'error' : 'success');
      onSummaryChange?.({
        version: result.version,
        total: result.results.length,
        failed,
      });
    } catch (reason) {
      setMessage(reason instanceof Error ? reason.message : '升级任务失败，请检查网络后重试');
      setMessageKind('error');
    } finally {
      setIsUpgrading(false);
    }
  }

  const availableCustomers = useMemo(
    () => customers.filter((customer) => customer.status === 'active' && customer.ip),
    [customers],
  );
  const targetCustomers = useMemo(
    () =>
      availableCustomers.filter(
        (customer) => targetEnvironment === 'all' || customer.environment === targetEnvironment,
      ),
    [availableCustomers, targetEnvironment],
  );
  const targetIDs = targetCustomers.map((customer) => customer.id);
  const allVisibleSelected = targetIDs.length > 0 && targetIDs.every((id) => selected.includes(id));
  const failedIDs = upgradeResults
    .filter((result) => !result.ok && result.id)
    .map((result) => result.id!);

  function toggleAllTargets() {
    setSelected((current) =>
      allVisibleSelected
        ? current.filter((id) => !targetIDs.includes(id))
        : [...new Set([...current, ...targetIDs])],
    );
  }

  return (
    <div className="page-content">
      <PageHeading
        eyebrow="RELEASE & DELIVERY"
        title="版本与升级"
        subtitle="按版本包、目标客户、执行结果的顺序完成发布。"
      >
        <button type="button" className="ghost-button" onClick={() => void load()}>
          <RefreshCw size={15} />
          刷新
        </button>
      </PageHeading>

      <ol className="upgrade-stepper" aria-label="升级流程">
        <li className="current">
          <span>01</span>
          <b>准备版本包</b>
        </li>
        <li className="step-connector" aria-hidden="true" />
        <li>
          <span>02</span>
          <b>选择目标</b>
        </li>
        <li className="step-connector" aria-hidden="true" />
        <li>
          <span>03</span>
          <b>执行与记录</b>
        </li>
      </ol>

      <Panel title="01 · 准备版本包" subtitle="上传后会校验 manifest、版本号和必要文件">
        <div className="release-grid">
          {releaseTypes.map((type) => (
            <section className="release-card" key={type}>
              <header>
                <div>
                  <b>{releaseLabels[type]}</b>
                  <small>
                    {releases[type]
                      ? `当前 v${releases[type]!.version} · ${dateTime(releases[type]!.uploadedAt)}`
                      : '尚未发布'}
                  </small>
                </div>
                <Tag good={Boolean(releases[type])} text={releases[type] ? '已发布' : '待上传'} />
              </header>
              <label className="release-file-field">
                <span>升级包</span>
                <input
                  type="file"
                  accept=".tar.gz"
                  onChange={(event) =>
                    setFiles((current) => ({ ...current, [type]: event.target.files?.[0] ?? null }))
                  }
                />
                <small>{files[type]?.name ?? '选择 .tar.gz 文件'}</small>
              </label>
              <label className="release-version-field">
                <span>版本号</span>
                <input
                  placeholder="例如 3.0.3"
                  value={versions[type]}
                  onChange={(event) =>
                    setVersions((current) => ({ ...current, [type]: event.target.value }))
                  }
                />
              </label>
              <button
                className="secondary"
                disabled={!files[type] || !versions[type]}
                onClick={() => void uploadRelease(type)}
              >
                <Package size={15} />
                上传并校验
              </button>
            </section>
          ))}
        </div>
        <details className="advanced-config">
          <summary>
            <ChevronDown size={15} />
            高级配置与包要求
          </summary>
          <div>
            <p>
              升级包需包含 manifest.json 与对应程序文件。系统校验包类型、版本号及
              SHA-256；客户升级端会备份并在失败时回滚。
            </p>
            <p>
              未配置客户 IP 的离线系统仍需在客户自己的升级页上传。发布过程保留原有协议与包格式。
            </p>
          </div>
        </details>
      </Panel>

      <Panel title="02 · 选择升级目标" subtitle="建议先选择测试环境验证，再推送生产环境">
        <div className="target-toolbar">
          <label>
            环境
            <select
              value={targetEnvironment}
              onChange={(event) => setTargetEnvironment(event.target.value)}
            >
              <option value="all">全部环境</option>
              <option value="test">测试</option>
              <option value="production">生产</option>
              <option value="unclassified">待分类</option>
            </select>
          </label>
          <span>
            {selected.length} 套已选择 · {targetCustomers.length} 套可选
          </span>
          <button type="button" className="link-button" onClick={toggleAllTargets}>
            {allVisibleSelected ? '取消全选' : '选择当前筛选'}
          </button>
        </div>
        {targetCustomers.length ? (
          <div className="target-table-wrap">
            <table className="target-table">
              <thead>
                <tr>
                  <th>选择</th>
                  <th>客户系统</th>
                  <th>环境</th>
                  <th>连接地址</th>
                  <th>当前版本</th>
                </tr>
              </thead>
              <tbody>
                {targetCustomers.map((customer) => (
                  <tr key={customer.id}>
                    <td>
                      <input
                        aria-label={`选择 ${customer.name}`}
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
                    </td>
                    <td>
                      <b>{customer.name}</b>
                      <small>{customer.systemId || '未登记系统 ID'}</small>
                    </td>
                    <td>{environmentLabel(customer.environment)}</td>
                    <td>
                      {customer.protocol}://{customer.ip}:{customer.port}
                    </td>
                    <td>
                      {customer.lastReport?.appVersion
                        ? `v${customer.lastReport.appVersion}`
                        : '未上报'}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <EmptyState
            icon={<MonitorCog size={20} />}
            title="暂无可升级客户"
            detail="请先创建客户并配置 IP，正常状态的客户会出现在这里。"
          />
        )}
      </Panel>

      <Panel title="03 · 执行与记录" subtitle="执行反馈来自客户系统；超时或失败目标可单独重试">
        <div className="execute-toolbar">
          <div>
            <b>升级所选目标</b>
            <small>当前选择 {selected.length} 套客户系统。执行前会再次确认版本与目标数量。</small>
          </div>
          <div className="execute-actions">
            {releaseTypes.map((type) => (
              <button
                key={type}
                className="primary"
                disabled={isUpgrading || selected.length === 0 || !releases[type]}
                onClick={() => void upgrade(type)}
              >
                <ArrowUpRight size={15} />
                推送{releaseLabels[type]}
              </button>
            ))}
          </div>
        </div>
        {isUpgrading && (
          <div className="upgrade-progress" role="status">
            <span className="spinner" />
            <div>
              <b>正在执行升级</b>
              <small>服务端正在逐台联系所选系统。完成前请勿重复提交。</small>
            </div>
          </div>
        )}
        {message && <p className={`inline-note upgrade-message ${messageKind}`}>{message}</p>}
        {upgradeResults.length > 0 && (
          <div className="upgrade-results">
            <div className="upgrade-results-heading">
              <div>
                <b>
                  最近一次执行 · {releaseLabels[upgradeType]} v{upgradeVersion}
                </b>
                <small>
                  {upgradeResults.length} 个目标 · {upgradeResults.filter((item) => item.ok).length}{' '}
                  个成功 · {upgradeResults.filter((item) => !item.ok).length} 个待处理
                </small>
              </div>
              {failedIDs.length > 0 && (
                <button
                  className="secondary"
                  disabled={isUpgrading}
                  onClick={() => void upgrade(upgradeType, failedIDs)}
                >
                  <RefreshCw size={14} />
                  重试失败目标（{failedIDs.length}）
                </button>
              )}
            </div>
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>客户系统</th>
                    <th>结果</th>
                    <th>状态 / 失败原因</th>
                  </tr>
                </thead>
                <tbody>
                  {upgradeResults.map((result, index) => (
                    <tr key={result.id ?? `${result.name}-${index}`}>
                      <td>
                        <b>{result.name}</b>
                      </td>
                      <td>
                        <Tag good={result.ok} text={result.ok ? '升级成功' : '待处理'} />
                      </td>
                      <td>
                        {result.error ??
                          (result.state === 'unverified'
                            ? '客户端已响应，版本尚未确认'
                            : result.ok
                              ? '版本已确认'
                              : '客户系统未能完成升级')}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        )}
      </Panel>
    </div>
  );
}

function environmentLabel(value: string) {
  return value === 'production' ? '生产' : value === 'test' ? '测试' : '待分类';
}
