import { BookOpen } from 'lucide-react';
import { useNavigate } from 'react-router-dom';

import { AdminHeader } from './shared';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { useTranslation } from '@/i18n/useI18n';

export function AdminSkillsPage() {
	const { t } = useTranslation();
	const navigate = useNavigate();
	return (
		<>
			<AdminHeader title={t('admin.nav.skills')} description={t('admin.skillsModuleDescription')} />
			<Card><CardHeader><CardTitle className="flex items-center gap-2"><BookOpen className="size-4" />{t('admin.skillsLibraryTitle')}</CardTitle><CardDescription>{t('admin.skillsLibraryDescription')}</CardDescription></CardHeader><CardContent><Button onClick={() => navigate('/skill')}>{t('admin.openSkillLibrary')}</Button></CardContent></Card>
			<div className="rounded-lg border border-dashed p-4 text-sm text-muted-foreground">{t('admin.skillsBoundaryNotice')}</div>
		</>
	);
}
