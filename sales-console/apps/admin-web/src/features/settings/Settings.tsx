import { useCallback, useEffect, useState } from 'react';
import { ClipboardList, KeyRound, Save, ShieldCheck } from 'lucide-react';
import { api } from '../../api/client';
import { dateTime } from '../../lib/format';
import { PageHeading, Panel } from '../../components/ui';
import type { SystemSection } from '../../types';
import { PublicKey } from './PublicKey';

export function Settings({
  refreshToken,
  section = 'business',
  embedded = false,
}: {
  refreshToken: number;
  section?: SystemSection;
  embedded?: boolean;
}) {
  const [rate, setRate] = useState('41841');
  const [currentPassword, setCurrentPassword] = useState('');
  const [newPassword, setNewPassword] = useState('');
  const [message, setMessage] = useState('');
  const [audit, setAudit] = useState<
    Array<{ at: number; actor: string; method: string; path: string }>
  >([]);
  const load = useCallback(() => {
    return api<{ tokenExchangeRate: number }>('/api/v1/settings')
      .then((result) => setRate(String(result.tokenExchangeRate)))
      .catch(() => undefined);
  }, []);
  useEffect(() => {
    if (section === 'business') void load();
  }, [load, refreshToken, section]);
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
    <div className={embedded ? 'embedded-page' : 'page-content'}>
      {!embedded && (
        <PageHeading
          eyebrow="SYSTEM GOVERNANCE"
          title="系统设置"
          subtitle="管理业务参数、安全和操作记录。"
        />
      )}
      {message && <p className="inline-note">{message}</p>}
      {section === 'business' && (
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
      )}
      {section === 'security' && (
        <div className="system-section-stack">
          <Panel title="登录安全" subtitle="密码修改需要当前密码验证">
            <div className="security-form">
              <label>
                当前密码
                <input
                  type="password"
                  autoComplete="current-password"
                  value={currentPassword}
                  onChange={(event) => setCurrentPassword(event.target.value)}
                />
              </label>
              <label>
                新密码
                <input
                  type="password"
                  autoComplete="new-password"
                  value={newPassword}
                  onChange={(event) => setNewPassword(event.target.value)}
                />
              </label>
              <button className="primary" onClick={savePassword}>
                <KeyRound size={15} />
                修改密码
              </button>
              <small>至少 8 位，需包含字母和数字。</small>
            </div>
          </Panel>
          <PublicKey refreshToken={refreshToken} embedded />
        </div>
      )}
      {section === 'audit' && (
        <div className="system-section-stack">
          <Panel title="审计原则" subtitle="敏感管理操作保留可追溯记录">
            <div className="audit-summary">
              <ShieldCheck size={20} />
              <span>
                记录操作者、接口、时间、来源 IP 和结果；不记录密码、Token 明文或请求正文。
              </span>
            </div>
          </Panel>
          <Panel title="操作记录" subtitle="查看最近 200 条管理操作">
            <div className="audit-summary">
              <span>
                <ShieldCheck size={18} /> 已记录操作者、接口、时间和来源 IP
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
      )}
    </div>
  );
}
