import { useCallback, useEffect, useState } from 'react';
import { api } from '../../api/client';
import { money, tokens } from '../../lib/format';
import { PageHeading, Panel } from '../../components/ui';

export function Reconciliation({
  refreshToken,
  embedded = false,
}: {
  refreshToken: number;
  embedded?: boolean;
}) {
  const [rows, setRows] = useState<
    Array<{
      name: string;
      rechargedAmount: number;
      rechargedTokens: number;
      consumedTokens: number | null;
    }>
  >([]);
  const load = useCallback(() => {
    return api<typeof rows>('/api/v1/reconciliation')
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
          eyebrow="FINANCE & USAGE"
          title="对账中心"
          subtitle="对比充值记录和客户系统上报的实际消耗。"
        />
      )}
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
