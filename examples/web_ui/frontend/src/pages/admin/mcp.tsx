import { Plug } from 'lucide-react';
import { useNavigate } from 'react-router-dom';

import { AdminHeader } from './shared';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { useTranslation } from '@/i18n/useI18n';

export function AdminMcpPage() {
	const { t } = useTranslation();
	const navigate = useNavigate();

	return (
		<>
			<AdminHeader title={t('admin.nav.mcp')} description={t('admin.mcpModuleDescription')} />
			<Card>
				<CardHeader>
					<CardTitle className="flex items-center gap-2">
						<Plug className="size-4" />
						{t('admin.mcpLibraryTitle')}
					</CardTitle>
					<CardDescription>{t('admin.mcpLibraryDescription')}</CardDescription>
				</CardHeader>
				<CardContent>
					<Button onClick={() => navigate('/mcp')}>{t('admin.openMcpLibrary')}</Button>
				</CardContent>
			</Card>
			<div className="rounded-lg border border-dashed p-4 text-sm text-muted-foreground">
				{t('admin.mcpConfigNotice')}
			</div>
		</>
	);
}
