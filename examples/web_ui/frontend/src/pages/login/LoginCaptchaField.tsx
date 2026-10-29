import { CircleAlert, RefreshCw } from 'lucide-react';

import { Alert, AlertDescription } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import { Field, FieldLabel } from '@/components/ui/field';
import { Input } from '@/components/ui/input';
import { useTranslation } from '@/i18n/useI18n';

interface LoginCaptchaFieldProps {
	value: string;
	imageDataUrl: string;
	expiresIn: number;
	loading: boolean;
	submitting: boolean;
	error?: string;
	onChange: (value: string) => void;
	onRefresh: () => void;
}

export function LoginCaptchaField({
	value,
	imageDataUrl,
	expiresIn,
	loading,
	submitting,
	error,
	onChange,
	onRefresh,
}: LoginCaptchaFieldProps) {
	const { t } = useTranslation();
	const disabled = loading || submitting;

	return (
		<Field>
			<FieldLabel htmlFor="login-captcha">{t('auth.captcha')}</FieldLabel>
			<div className="flex min-w-0 items-center gap-2">
				<Input
					id="login-captcha"
					type="text"
					autoComplete="one-time-code"
					autoCapitalize="characters"
					aria-describedby="login-captcha-hint"
					className="min-w-0 flex-1 uppercase tracking-[0.2em]"
					value={value}
					onChange={(event) => onChange(event.target.value.toUpperCase().slice(0, 5))}
					placeholder={t('auth.captchaPlaceholder')}
					maxLength={5}
					spellCheck={false}
					disabled={disabled}
					required
				/>
				<button
					type="button"
					onClick={onRefresh}
					disabled={disabled}
					aria-label={t('auth.refreshCaptchaImage')}
					className="shrink-0 rounded-md outline-none focus-visible:ring-2 focus-visible:ring-ring"
				>
					<img
						src={imageDataUrl}
						alt={t('auth.captchaImageAlt')}
						className="h-10 w-[118px] rounded-md border border-border object-cover"
					/>
				</button>
				<Button
					type="button"
					variant="ghost"
					size="icon"
					className="size-9 shrink-0"
					onClick={onRefresh}
					disabled={disabled}
					aria-label={t('auth.refreshCaptcha')}
				>
					<RefreshCw className={`size-4 ${loading ? 'animate-spin' : ''}`} />
				</Button>
			</div>
			<p id="login-captcha-hint" className="text-xs text-muted-foreground">
				{t('auth.captchaHint', { seconds: expiresIn })}
			</p>
			{error && (
				<Alert variant="destructive">
					<CircleAlert />
					<AlertDescription>{error}</AlertDescription>
				</Alert>
			)}
		</Field>
	);
}
