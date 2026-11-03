import { Check, Copy } from 'lucide-react';
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
import { useTranslation } from '@/i18n/useI18n';

interface InfoDialogProps {
	open: boolean;
	onOpenChange: (open: boolean) => void;
	title: string;
	description?: ReactNode;
	/** Body content (e.g. a credential card or a `<pre>` of JSON). */
	children?: ReactNode;
	/** When set, shows a copy button that writes this text to clipboard. */
	copyText?: string;
	closeLabel?: string;
}

/**
 * A project-flavored replacement for result-style `alert` calls (e.g. a
 * freshly generated temporary password or an audit overview payload).
 * Renders inside the app's own `Dialog` so it stays themeable and
 * translatable, and lets the user copy the content instead of squinting
 * at a native alert box.
 */
export function InfoDialog({
	open,
	onOpenChange,
	title,
	description,
	children,
	copyText,
	closeLabel,
}: InfoDialogProps) {
	const { t } = useTranslation();
	const [copied, setCopied] = useState(false);

	const handleCopy = async () => {
		if (!copyText) return;
		try {
			await navigator.clipboard.writeText(copyText);
			setCopied(true);
			window.setTimeout(() => setCopied(false), 1500);
		} catch {
			// Clipboard may be unavailable (insecure context) — silently ignore.
		}
	};

	return (
		<Dialog open={open} onOpenChange={onOpenChange}>
			<DialogContent className="max-w-md">
				<DialogHeader>
					<DialogTitle>{title}</DialogTitle>
					{description && <DialogDescription>{description}</DialogDescription>}
				</DialogHeader>
				{children && <div className="space-y-3">{children}</div>}
				<DialogFooter>
					{copyText && (
						<Button variant="outline" onClick={handleCopy} type="button">
							{copied ? <Check className="size-4" /> : <Copy className="size-4" />}
							{copied ? t('common.copied') : t('common.copy')}
						</Button>
					)}
					<Button onClick={() => onOpenChange(false)} type="button">
						{closeLabel ?? t('common.close')}
					</Button>
				</DialogFooter>
			</DialogContent>
		</Dialog>
	);
}
