import { useCallback, useEffect, useState } from 'react';
import { Copy } from 'lucide-react';
import { api } from '../../api/client';
import { PageHeading, Panel } from '../../components/ui';

export function PublicKey({
  refreshToken,
  embedded = false,
}: {
  refreshToken: number;
  embedded?: boolean;
}) {
  const [key, setKey] = useState('');
  const [fingerprint, setFingerprint] = useState('');
  const load = useCallback(() => {
    return api<{ publicKeyPem: string; publicKeyFingerprint?: string }>('/api/v1/public-key')
      .then((result) => {
        setKey(result.publicKeyPem);
        setFingerprint(result.publicKeyFingerprint ?? '');
      })
      .catch(() => undefined);
  }, []);
  useEffect(() => {
    void load();
  }, [load, refreshToken]);
  return (
    <div className={embedded ? 'embedded-page' : 'page-content'}>
      {!embedded && (
        <PageHeading
          eyebrow="TRUST CONFIGURATION"
          title="总部公钥"
          subtitle="客户系统使用它在本地验证销售总台签发的充值码。"
        />
      )}
      <Panel title="Sales Hub Ed25519 公钥" subtitle="公钥可以分发，不属于机密信息">
        <div className="key-box">
          <textarea value={key} readOnly rows={8} />
          {fingerprint && <small>指纹：{fingerprint}</small>}
          <button className="secondary" onClick={() => navigator.clipboard.writeText(key)}>
            <Copy size={15} />
            复制公钥
          </button>
        </div>
      </Panel>
    </div>
  );
}
