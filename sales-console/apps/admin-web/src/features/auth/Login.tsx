import { useState, type FormEvent } from 'react';
import { api } from '../../api/client';
import type { Staff } from '../../types';

export function Login({ onLoggedIn }: { onLoggedIn: (staff: Staff) => void }) {
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
        <div className="brand-mark large login-brand-mark">
          <img src="/longxin-logo.png" alt="龙信" />
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
