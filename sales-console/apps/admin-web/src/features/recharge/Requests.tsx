import { useCallback, useEffect, useState } from 'react';
import { Check, ClipboardList, RefreshCw, X } from 'lucide-react';
import { api } from '../../api/client';
import { dateTime, money } from '../../lib/format';
import { EmptyState, PageHeading, Panel } from '../../components/ui';

export function Requests({
  refreshToken,
  embedded = false,
}: {
  refreshToken: number;
  embedded?: boolean;
}) {
  const [orders, setOrders] = useState<
    Array<{
      id: string;
      customerName: string;
      requestedAmount?: number | null;
      note?: string;
      createdAt: number;
      status: string;
      requestId?: string;
    }>
  >([]);
  const [error, setError] = useState('');
  const [processingId, setProcessingId] = useState<string | null>(null);
  const load = useCallback(() => {
    setError('');
    return api<typeof orders>('/api/v1/recharge-requests?status=pending')
      .then(setOrders)
      .catch((reason) => setError(reason instanceof Error ? reason.message : '读取失败'));
  }, []);
  useEffect(() => {
    const timer = window.setTimeout(() => void load(), 0);
    return () => window.clearTimeout(timer);
  }, [load, refreshToken]);
  async function process(
    id: string,
    action: 'approve' | 'reject',
    requestedAmount?: number | null,
    requestId?: string,
  ) {
    const amount =
      action === 'approve'
        ? Number(window.prompt('确认充值金额（元）', String(requestedAmount ?? '')))
        : undefined;
    if (action === 'approve' && (!amount || amount <= 0)) return;
    if (processingId) return;
    setProcessingId(id);
    setError('');
    try {
      await api(`/api/v1/recharge-requests/${id}/${action}`, {
        method: 'POST',
        body: JSON.stringify({
          ...(action === 'approve' ? { amount: Number(amount).toFixed(2) } : {}),
          ...(requestId ? { request_id: requestId } : {}),
        }),
      });
      load();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '操作失败');
    } finally {
      setProcessingId(null);
    }
  }
  return (
    <div className={embedded ? 'embedded-page' : 'page-content'}>
      {!embedded && (
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
      )}
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
                        disabled={processingId !== null}
                        onClick={() =>
                          process(order.id, 'approve', order.requestedAmount, order.requestId)
                        }
                      >
                        <Check size={14} />
                        批准
                      </button>
                      <button
                        className="danger small"
                        disabled={processingId !== null}
                        onClick={() => process(order.id, 'reject', undefined, order.requestId)}
                      >
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
