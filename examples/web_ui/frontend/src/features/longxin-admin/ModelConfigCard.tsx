import { ArrowRight, KeyRound, SlidersHorizontal } from 'lucide-react';
import { useNavigate } from 'react-router-dom';

import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { useTranslation } from '@/i18n/useI18n';

/**
 * Admin entry point for model configuration.
 *
 * Credential and provider configuration already live in the shared credential
 * center. Keep this card as a small admin-facing entry point instead of
 * duplicating that configuration flow inside the admin page.
 */
export function ModelConfigCard() {
	const { t } = useTranslation();
	const navigate = useNavigate();

	return (
		<Card>
			<CardHeader>
				<CardTitle className="flex items-center gap-2">
					<SlidersHorizontal className="size-4" />
					{t('admin.modelConfig')}
				</CardTitle>
				<CardDescription>{t('admin.modelConfigDescription')}</CardDescription>
			</CardHeader>
			<CardContent className="space-y-4">
				<div className="grid gap-2 sm:grid-cols-3">
					<Badge variant="secondary" className="justify-center py-1.5">
						{t('admin.modelTypes.chat')}
					</Badge>
					<Badge variant="secondary" className="justify-center py-1.5">
						{t('admin.modelTypes.tts')}
					</Badge>
					<Badge variant="secondary" className="justify-center py-1.5">
						{t('admin.modelTypes.embedding')}
					</Badge>
				</div>
				<div className="flex flex-wrap items-center justify-between gap-3 rounded-lg border bg-muted/30 p-3">
					<p className="text-sm text-muted-foreground">
						{t('admin.modelConfigCredentialHint')}
					</p>
					<Button type="button" variant="outline" onClick={() => navigate('/admin/models/config')}>
						<KeyRound />
						{t('admin.openCredentialConfig')}
						<ArrowRight />
					</Button>
				</div>
			</CardContent>
		</Card>
	);
}
