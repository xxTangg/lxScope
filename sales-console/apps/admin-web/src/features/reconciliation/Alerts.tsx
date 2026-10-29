import { useCallback, useEffect, useState } from 'react';
import { AlertTriangle, ShieldCheck } from 'lucide-react';
import { api } from '../../api/client';
import { tokens } from '../../lib/format';
import { EmptyState, PageHeading, Panel } from '../../components/ui';

export function Alerts({
  refreshToken,
  embedded = false,
}: {
  refreshToken: number;
  embedded?: boolean;
}) {
  const [rows, setRows] = useState<
    Array<{ name: string; poolTokens: number; daysLeft: number | null }>
  >([]);
  const load = useCallback(() => {
    return api<typeof rows>('/api/v1/alerts')
      .then(setRows)
      .catch(() => undefined);
  }, []);
  useEffect(() => {
    void load();
  }, [load, refreshToken]);
  return (
    <div className={embedded ? 'embedded-page' : 'page-content'}>
      {!embedded && (
        <PageHeading
          eyebrow="CUSTOMER SUCCESS"
          title="余额预警"
          subtitle="按最近上报估算消耗速度，预计 14 天内耗尽的客户会出现在这里。"
        />
      )}
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
