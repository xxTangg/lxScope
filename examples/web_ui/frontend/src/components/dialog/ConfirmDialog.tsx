import { CircleAlert, Loader2 } from 'lucide-react';
import { useState, type ReactNode } from 'react';

import { Button } from '@/components/ui/button';
import {
	Dialog,
	DialogContent,
	DialogDescription,
	DialogFooter,
	DialogHeader,
	DialogTitle,
} from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Textarea } from '@/components/ui/textarea';
import { useTranslation } from '@/i18n/useI18n';

/** Values collected from a {@link ConfirmDialog} on confirm. */
export interface ConfirmDialogValues {
	/** Trimmed reason text when `requireReason` was set. */
	reason?: string;
	/** Raw secret text when `requireSecret` was set (e.g. admin password). */
	secret?: string;
}

interface ConfirmDialogProps {
	open: boolean;
	onOpenChange: (open: boolean) => void;
	title: string;
	description?: ReactNode;
	confirmLabel?: string;
	cancelLabel?: string;
	/** `destructive` paints the confirm button red. */
	variant?: 'default' | 'destructive';
	/** Disables the buttons while an async action is in flight. */
	loading?: boolean;
	/** Show a required reason textarea (min `reasonMinLength`). */
	requireReason?: boolean;
	reasonLabel?: string;
	reasonPlaceholder?: string;
	reasonMinLength?: number;
	/** Show a required secret input (e.g. admin password). */
	requireSecret?: boolean;
	secretLabel?: string;
	secretPlaceholder?: string;
	/** Called with the collected values; close is handled by the owner. */
	onConfirm: (values: ConfirmDialogValues) => void | Promise<void>;
}

/**
 * A project-flavored replacement for the native `confirm` / `prompt`
 * dialogs. Renders inside the app's own `Dialog` so it follows the
 * theme and is translatable. Supports an optional required reason
 * textarea and an optional secret input (password field), which covers
 * the two `window.prompt` call sites (reason + admin password) without
 * spawning bespoke components.
 *
 * The owner keeps ownership of `open` — this dialog never closes itself
 * on confirm, so success handling (and the resulting close) stays with
 * the caller.
 */
export function ConfirmDialog({
	open,
	onOpenChange,
	title,
	description,
	confirmLabel,
	cancelLabel,
	variant = 'default',
	loading = false,
	requireReason = false,
	reasonLabel,
	reasonPlaceholder,
	reasonMinLength = 4,
	requireSecret = false,
	secretLabel,
	secretPlaceholder,
	onConfirm,
}: ConfirmDialogProps) {
	const { t } = useTranslation();
	const [reason, setReason] = useState('');
	const [secret, setSecret] = useState('');
	const [reasonError, setReasonError] = useState(false);

	const reset = () => {
		setReason('');
		setSecret('');
		setReasonError(false);
	};

	const handleOpenChange = (next: boolean) => {
		if (!next) reset();
		onOpenChange(next);
	};

	const handleConfirm = async () => {
		if (requireReason && reason.trim().length < reasonMinLength) {
			setReasonError(true);
			return;
		}
		setReasonError(false);
		await onConfirm({
			reason: requireReason ? reason.trim() : undefined,
			secret: requireSecret ? secret : undefined,
		});
	};

	return (
		<Dialog open={open} onOpenChange={handleOpenChange}>
			<DialogContent className="max-w-md">
				<DialogHeader>
					<DialogTitle>{title}</DialogTitle>
					{description && <DialogDescription>{description}</DialogDescription>}
				</DialogHeader>
				<div className="space-y-3">
					{requireSecret && (
						<div className="space-y-1.5">
							<Label htmlFor="confirm-dialog-secret">
								{secretLabel ?? t('common.confirmSecretLabel')}
							</Label>
							<Input
								id="confirm-dialog-secret"
								type="password"
								autoComplete="off"
								value={secret}
								onChange={(event) => setSecret(event.target.value)}
								placeholder={secretPlaceholder}
								disabled={loading}
							/>
						</div>
					)}
					{requireReason && (
						<div className="space-y-1.5">
							<Label htmlFor="confirm-dialog-reason">
								{reasonLabel ?? t('common.reason')}
							</Label>
							<Textarea
								id="confirm-dialog-reason"
								value={reason}
								onChange={(event) => {
									setReason(event.target.value);
									if (reasonError) setReasonError(false);
								}}
								placeholder={reasonPlaceholder}
								maxLength={300}
								className="min-h-20 resize-y"
								disabled={loading}
							/>
							{reasonError && (
								<p className="text-xs text-destructive">{t('common.reasonRequired')}</p>
							)}
						</div>
					)}
				</div>
				<DialogFooter>
					<Button variant="ghost" onClick={() => handleOpenChange(false)} disabled={loading}>
						<CircleAlert className="size-3.5" />
						{cancelLabel ?? t('common.cancel')}
					</Button>
					<Button
						variant={variant === 'destructive' ? 'destructive' : 'default'}
						onClick={handleConfirm}
						disabled={loading}
						autoFocus
					>
						{loading && <Loader2 className="size-3.5 animate-spin" />}
						{confirmLabel ?? t('common.confirm')}
					</Button>
				</DialogFooter>
			</DialogContent>
		</Dialog>
	);
}
