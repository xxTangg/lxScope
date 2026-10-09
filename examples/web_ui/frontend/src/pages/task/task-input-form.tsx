import { MessageSquareText } from 'lucide-react';
import * as React from 'react';

import type { TaskInputRequest } from '@/api';
import { Button } from '@/components/ui/button';
import { Textarea } from '@/components/ui/textarea';

export function TaskInputForm({
	request,
	onAnswer,
}: {
	request: TaskInputRequest;
	onAnswer: (requestId: string, answers: Record<string, string>) => Promise<void>;
}) {
	const [answers, setAnswers] = React.useState<Record<string, string>>({});
	const [submitting, setSubmitting] = React.useState(false);
	const [error, setError] = React.useState<string | null>(null);
	const missing = request.questions.some(
		(question) => question.required && !answers[question.key]?.trim(),
	);

	return (
		<form
			className="space-y-3 rounded-xl border border-amber-200 bg-amber-50/60 p-3"
			onSubmit={async (event) => {
				event.preventDefault();
				if (submitting || missing) return;
				setSubmitting(true);
				setError(null);
				try {
					await onAnswer(request.id, answers);
				} catch (cause) {
					setError(cause instanceof Error ? cause.message : '提交失败，请重试。');
				} finally {
					setSubmitting(false);
				}
			}}
		>
			<div className="flex items-center gap-2 text-sm font-semibold text-amber-900">
				<MessageSquareText className="size-4" />
				需要你补充信息
			</div>
			<p className="text-xs leading-5 text-slate-600">
				{request.reason || '当前步骤正在等待你的回答。'} 回答后将继续本次运行。
			</p>
			{request.questions.map((question) => (
				<div key={question.key} className="space-y-2">
					<label
						htmlFor={`task-input-${request.id}-${question.key}`}
						className="block text-xs font-medium text-slate-800"
					>
						{question.label}
						{question.required && <span className="ml-1 text-red-600">*</span>}
					</label>
					{question.options.length > 0 && (
						<div className="flex flex-wrap gap-1">
							{question.options.map((option) => (
								<Button
									key={option}
									type="button"
									size="sm"
									variant={
										answers[question.key] === option ? 'default' : 'outline'
									}
									disabled={submitting}
									onClick={() =>
										setAnswers((current) => ({
											...current,
											[question.key]: option,
										}))
									}
								>
									{option}
								</Button>
							))}
						</div>
					)}
					<Textarea
						id={`task-input-${request.id}-${question.key}`}
						value={answers[question.key] ?? ''}
						required={question.required}
						disabled={submitting}
						rows={2}
						placeholder="填写信息或补充资料"
						onChange={(event) =>
							setAnswers((current) => ({
								...current,
								[question.key]: event.target.value,
							}))
						}
					/>
				</div>
			))}
			{error && (
				<p role="alert" className="text-xs text-red-700">
					{error}
				</p>
			)}
			<Button
				type="submit"
				className="w-full"
				disabled={
					submitting || missing || !Object.values(answers).some((answer) => answer.trim())
				}
			>
				{submitting ? '提交中…' : '提交并继续'}
			</Button>
		</form>
	);
}
