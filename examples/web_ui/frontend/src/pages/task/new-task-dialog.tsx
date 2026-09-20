import { CircleAlert, Loader2, Sparkles, X } from 'lucide-react';
import * as React from 'react';

import { Alert, AlertDescription } from '@/components/ui/alert';
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
import { Textarea } from '@/components/ui/textarea';

interface NewTaskDialogProps {
	open: boolean;
	onOpenChange: (open: boolean) => void;
	onCreate: (input: { title: string; goal: string }) => Promise<void>;
}

export function NewTaskDialog({ open, onOpenChange, onCreate }: NewTaskDialogProps) {
	const [title, setTitle] = React.useState('');
	const [goal, setGoal] = React.useState('');
	const [error, setError] = React.useState('');
	const [isSubmitting, setIsSubmitting] = React.useState(false);

	React.useEffect(() => {
		if (!open) return;
		setTitle('');
		setGoal('');
		setError('');
		setIsSubmitting(false);
	}, [open]);

	const handleSubmit = async (event: React.FormEvent<HTMLFormElement>) => {
		event.preventDefault();
		const normalizedGoal = goal.trim();
		if (!normalizedGoal) {
			setError('请先填写任务目的。');
			return;
		}
		setError('');
		setIsSubmitting(true);
		try {
			await onCreate({ title: title.trim(), goal: normalizedGoal });
			onOpenChange(false);
		} catch (requestError) {
			setError(requestError instanceof Error ? requestError.message : '任务创建失败。');
		} finally {
			setIsSubmitting(false);
		}
	};

	return (
		<Dialog open={open} onOpenChange={onOpenChange}>
			<DialogContent className="!w-[560px] !max-w-[560px]">
				<DialogHeader>
					<DialogTitle>新建任务</DialogTitle>
					<DialogDescription>
						先描述想完成的目标，系统会自动生成可编辑的线性任务流，并根据需要组合 AgentStep、ToolStep 和 PythonStep。
					</DialogDescription>
				</DialogHeader>

				<form onSubmit={handleSubmit} className="space-y-5">
					<label className="block space-y-2">
						<span className="text-sm font-medium">任务名称（可选）</span>
						<Input
							value={title}
							onChange={(event) => setTitle(event.target.value)}
							placeholder="留空则由 AI 根据任务目的生成"
							disabled={isSubmitting}
						/>
					</label>
					<label className="block space-y-2">
						<span className="text-sm font-medium">任务目的</span>
						<Textarea
							value={goal}
							onChange={(event) => setGoal(event.target.value)}
							placeholder="例如：制定一次从上海到北京的三天出差规划，并整理成可执行的行程。"
							className="min-h-32 resize-y"
							disabled={isSubmitting}
							autoFocus
						/>
					</label>
					{error && (
						<Alert variant="destructive">
							<CircleAlert />
							<AlertDescription>{error}</AlertDescription>
						</Alert>
					)}
					<DialogFooter>
						<Button
							type="button"
							variant="ghost"
							onClick={() => onOpenChange(false)}
							disabled={isSubmitting}
						>
							<X className="size-3.5" />
							取消
						</Button>
						<Button type="submit" disabled={isSubmitting || !goal.trim()}>
							{isSubmitting ? (
								<Loader2 className="size-3.5 animate-spin" />
							) : (
								<Sparkles className="size-3.5" />
							)}
							创建并生成节点
						</Button>
					</DialogFooter>
				</form>
			</DialogContent>
		</Dialog>
	);
}
