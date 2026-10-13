import { ClipboardPaste, MessageSquareText } from 'lucide-react';
import * as React from 'react';

import type { TaskInputRequest } from '@/api';
import { Button } from '@/components/ui/button';
import { Textarea } from '@/components/ui/textarea';
import {
	containsTaskTemplateDemoPlaceholder,
	formatTaskTemplateDemoAnswer,
	getTaskTemplateDemoInput,
} from './task-template-demo-inputs';

function isJsonArrayOrObject(value: string): boolean {
	try {
		const parsed: unknown = JSON.parse(value);
		return Boolean(parsed) && typeof parsed === 'object';
	} catch {
		return false;
	}
}

export function TaskInputForm({
	request,
	templateId,
	onAnswer,
}: {
	request: TaskInputRequest;
	templateId: string | null;
	onAnswer: (requestId: string, answers: Record<string, string>) => Promise<void>;
}) {
	const [answers, setAnswers] = React.useState<Record<string, string>>({});
	const [submitting, setSubmitting] = React.useState(false);
	const [error, setError] = React.useState<string | null>(null);
	const demoInput = React.useMemo(
		() => (templateId ? getTaskTemplateDemoInput(templateId) : null),
		[templateId],
	);
	const demoEntries = React.useMemo(() => {
		if (!demoInput) return [];
		return request.questions.flatMap((question) => {
			if (!(question.key in demoInput.inputs)) return [];
			const value = demoInput.inputs[question.key];
			if (containsTaskTemplateDemoPlaceholder(value)) return [];
			const answer = formatTaskTemplateDemoAnswer(question.key, value);
			return answer === null ? [] : [{ key: question.key, answer }];
		});
	}, [demoInput, request.questions]);
	const hasSkippedPlaceholder = Boolean(
		demoInput &&
		request.questions.some(
			(question) =>
				question.key in demoInput.inputs &&
				containsTaskTemplateDemoPlaceholder(demoInput.inputs[question.key]),
		),
	);
	const missing = request.questions.some(
		(question) => question.required && !answers[question.key]?.trim(),
	);

	const fillDemoEntry = (key: string) => {
		const entry = demoEntries.find((item) => item.key === key);
		if (!entry) return;
		setAnswers((current) => ({ ...current, [key]: entry.answer }));
		setError(null);
	};

	const fillAllDemoEntries = () => {
		setAnswers((current) => ({
			...current,
			...Object.fromEntries(demoEntries.map(({ key, answer }) => [key, answer])),
		}));
		setError(null);
	};

	return (
		<form
			className="space-y-3 rounded-xl border border-amber-200 bg-amber-50/60 p-3"
			onSubmit={async (event) => {
				event.preventDefault();
				if (submitting || missing) return;
				const invalidJsonQuestion = request.questions.find(
					(question) =>
						question.key.endsWith('_json') &&
						answers[question.key]?.trim() &&
						!isJsonArrayOrObject(answers[question.key]),
				);
				if (invalidJsonQuestion) {
					setError(
						`“${invalidJsonQuestion.label}”请填写 JSON 数组或对象，不要再套一层字符串引号。`,
					);
					return;
				}
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
			{demoInput && (demoEntries.length > 0 || hasSkippedPlaceholder) && (
				<div className="rounded-lg border border-blue-100 bg-blue-50/70 p-2.5">
					<div className="flex flex-wrap items-center justify-between gap-2">
						<span className="text-[11px] font-medium text-blue-900">
							可选演示输入 · v{demoInput.template_version}
						</span>
						{demoEntries.length > 0 && (
							<Button
								type="button"
								variant="outline"
								size="sm"
								className="h-7 px-2 text-[10px]"
								disabled={submitting}
								onClick={fillAllDemoEntries}
							>
								<ClipboardPaste />
								填入全部匹配项（{demoEntries.length}）
							</Button>
						)}
					</div>
					{hasSkippedPlaceholder && (
						<p className="mt-1 text-[10px] leading-4 text-amber-800">
							示例中的服务端授权资料 ID
							占位值已跳过，请填写当前用户有权访问的真实资料。
						</p>
					)}
				</div>
			)}
			{request.questions.map((question) => (
				<div key={question.key} className="space-y-2">
					<div className="flex flex-wrap items-center justify-between gap-2">
						<label
							htmlFor={`task-input-${request.id}-${question.key}`}
							className="block text-xs font-medium text-slate-800"
						>
							{question.label}
							{question.required && <span className="ml-1 text-red-600">*</span>}
						</label>
						{demoEntries.some((entry) => entry.key === question.key) && (
							<Button
								type="button"
								size="sm"
								variant="ghost"
								className="h-7 px-2 text-[10px] text-blue-700"
								disabled={submitting}
								onClick={() => fillDemoEntry(question.key)}
							>
								填入示例
							</Button>
						)}
					</div>
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
						rows={question.key.endsWith('_json') ? 8 : 2}
						placeholder="填写信息或补充资料"
						className={question.key.endsWith('_json') ? 'font-mono text-xs' : undefined}
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
