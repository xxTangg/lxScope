import { PageHeading, PageTabs } from '../../components/ui';
import type { SystemSection } from '../../types';
import { Settings } from './Settings';

const sections: Array<{ id: SystemSection; label: string }> = [
  { id: 'business', label: '业务参数' },
  { id: 'security', label: '安全与公钥' },
  { id: 'audit', label: '审计记录' },
];

export function SystemManagement({
  refreshToken,
  activeSection,
  onSectionChange,
}: {
  refreshToken: number;
  activeSection: SystemSection;
  onSectionChange: (section: SystemSection) => void;
}) {
  return (
    <div className="page-content">
      <PageHeading
        eyebrow="SYSTEM GOVERNANCE"
        title="系统管理"
        subtitle="按业务参数、安全配置和审计记录分类管理。"
      />
      <PageTabs
        label="系统管理分类"
        tabs={sections}
        value={activeSection}
        onChange={onSectionChange}
      />
      <Settings refreshToken={refreshToken} section={activeSection} embedded />
    </div>
  );
}
