import { useCallback, useEffect, useMemo, useState } from 'react';
import { Search } from 'lucide-react';
import { api } from '../../api/client';
import { PageHeading, PageTabs, Panel, Tag, EmptyState } from '../../components/ui';
import { dateTime, money } from '../../lib/format';
import type { Customer, FinanceSection } from '../../types';
import { Requests } from '../recharge/Requests';
import { Reconciliation } from '../reconciliation/Reconciliation';
import { Alerts } from '../reconciliation/Alerts';

type RechargeOrder = {
  id: string;
  method: string;
  requestedAmount?: number | null;
  amount?: number;
  status: string;
  processedBy?: string;
  createdAt: number;
};

const sections: Array<{ id: FinanceSection; label: string }> = [
  { id: 'requests', label: '充值申请' },
  { id: 'history', label: '充值记录' },
  { id: 'reconciliation', label: '用量对账' },
  { id: 'alerts', label: '余额预警' },
];

function RechargeHistory({ refreshToken }: { refreshToken: number }) {
  const [customers, setCustomers] = useState<Customer[]>([]);
  const [customerId, setCustomerId] = useState('');
  const [customerSearch, setCustomerSearch] = useState('');
  const [status, setStatus] = useState('all');
  const [orders, setOrders] = useState<RechargeOrder[]>([]);
  const [error, setError] = useState('');
  const [loadedCustomerId, setLoadedCustomerId] = useState('');

  const loadCustomers = useCallback(() => {
    return api<Customer[]>('/api/v1/customers')
      .then((next) => {
        setCustomers(next);
        setCustomerId((current) =>
          current && next.some((customer) => customer.id === current) ? current : '',
        );
      })
      .catch((reason) => setError(reason instanceof Error ? reason.message : '客户列表读取失败'));
  }, []);

  useEffect(() => {
    void loadCustomers();
  }, [loadCustomers, refreshToken]);

  useEffect(() => {
    if (!customerId) return;
    let ignore = false;
    api<RechargeOrder[]>(`/api/v1/customers/${customerId}/recharge-orders`)
      .then((nextOrders) => {
        if (ignore) return;
        setOrders(nextOrders);
        setLoadedCustomerId(customerId);
      })
      .catch((reason) => {
        if (ignore) return;
        setError(reason instanceof Error ? reason.message : '充值记录读取失败');
        setLoadedCustomerId(customerId);
      });
    return () => {
      ignore = true;
    };
  }, [customerId, refreshToken]);

  const visibleOrders = useMemo(
    () =>
      [...orders]
        .filter((order) => status === 'all' || order.status === status)
        .sort((a, b) => b.createdAt - a.createdAt),
    [orders, status],
  );

  return (
    <Panel title="客户充值记录" subtitle="选择客户后查看在线申请与离线充值码的统一时间线">
      <div className="table-toolbar">
        <label className="search-field">
          <Search size={16} aria-hidden="true" />
          <input
            value={customerSearch}
            onChange={(event) => setCustomerSearch(event.target.value)}
            placeholder="筛选客户名称或系统 ID"
            aria-label="筛选客户名称或系统 ID"
          />
        </label>
        <select
          aria-label="选择客户"
          value={customerId}
          onChange={(event) => {
            setOrders([]);
            setLoadedCustomerId('');
            setError('');
            setCustomerId(event.target.value);
          }}
        >
          <option value="">选择客户</option>
          {customers
            .filter((customer) =>
              `${customer.name} ${customer.systemId}`
                .toLocaleLowerCase()
                .includes(customerSearch.trim().toLocaleLowerCase()),
            )
            .map((customer) => (
              <option key={customer.id} value={customer.id}>
                {customer.name} · {customer.systemId || '未登记系统 ID'}
              </option>
            ))}
        </select>
        <select
          aria-label="充值记录状态筛选"
          value={status}
          onChange={(event) => setStatus(event.target.value)}
        >
          <option value="all">全部状态</option>
          <option value="pending">待审核</option>
          <option value="approved">已批准</option>
          <option value="issued">已签发</option>
          <option value="rejected">已拒绝</option>
        </select>
        <span className="toolbar-count">{visibleOrders.length} 条记录</span>
      </div>
      {error && <p className="form-error">{error}</p>}
      {customerId && loadedCustomerId === customerId && visibleOrders.length > 0 ? (
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
              </tr>
            </thead>
            <tbody>
              {visibleOrders.map((order) => (
                <tr key={order.id}>
                  <td>{dateTime(order.createdAt)}</td>
                  <td>{order.method === 'online' ? '在线申请' : '离线充值码'}</td>
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
          icon={<Search size={19} />}
          title={
            customerId && loadedCustomerId !== customerId
              ? '正在读取记录…'
              : customerId
                ? '暂无匹配充值记录'
                : '先选择一个客户'
          }
          detail={
            customerId && loadedCustomerId !== customerId
              ? '正在加载该客户的充值订单。'
              : customerId
                ? '可调整状态筛选，或到客户详情中查看订单和重签充值码。'
                : '选择客户后即可查询在线充值与离线充值码历史。'
          }
        />
      )}
    </Panel>
  );
}

export function Finance({
  refreshToken,
  activeSection,
  onSectionChange,
}: {
  refreshToken: number;
  activeSection: FinanceSection;
  onSectionChange: (section: FinanceSection) => void;
}) {
  return (
    <div className="page-content">
      <PageHeading
        eyebrow="FINANCE & CUSTOMER SUCCESS"
        title="充值与财务"
        subtitle="优先处理待审核申请；历史记录、用量和余额预警按需查询。"
      />
      <PageTabs
        label="充值与财务工作区"
        tabs={sections}
        value={activeSection}
        onChange={onSectionChange}
      />
      {activeSection === 'requests' && <Requests refreshToken={refreshToken} embedded />}
      {activeSection === 'history' && <RechargeHistory refreshToken={refreshToken} />}
      {activeSection === 'reconciliation' && (
        <Reconciliation refreshToken={refreshToken} embedded />
      )}
      {activeSection === 'alerts' && <Alerts refreshToken={refreshToken} embedded />}
    </div>
  );
}
