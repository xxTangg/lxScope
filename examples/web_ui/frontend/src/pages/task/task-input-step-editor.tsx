import type { InputStepConfig } from '@/api';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';

export function TaskInputStepEditor({
	config,
	disabled,
	onChange,
}: {
	config: InputStepConfig;
	disabled: boolean;
	onChange: (config: InputStepConfig) => void;
}) {
	return (
		<div className="space-y-3">
			<Input
				aria-label="补充信息说明"
				value={config.reason}
				disabled={disabled}
				placeholder="为什么需要补充信息"
				onChange={(event) => onChange({ ...config, reason: event.target.value })}
			/>
			{config.questions.map((question, index) => {
				const update = (changes: Partial<typeof question>) =>
					onChange({
						...config,
						questions: config.questions.map((item, position) =>
							position === index ? { ...item, ...changes } : item,
						),
					});
				return (
					<div key={index} className="space-y-2 rounded-lg border border-border p-3">
						<Input
							aria-label={`问题 ${index + 1}`}
							value={question.label}
							disabled={disabled}
							placeholder="需要用户提供什么信息"
							onChange={(event) => update({ label: event.target.value })}
						/>
						<Input
							aria-label={`字段名 ${index + 1}`}
							value={question.key}
							disabled={disabled}
							placeholder="字段名，例如 destination"
							onChange={(event) => update({ key: event.target.value })}
						/>
						<Input
							aria-label={`建议选项 ${index + 1}`}
							value={question.options.join('、')}
							disabled={disabled}
							placeholder="建议选项，以顿号分隔（可不填）"
							onChange={(event) =>
								update({ options: event.target.value.split('、').filter(Boolean) })
							}
						/>
						<div className="flex items-center justify-between text-xs">
							<label className="flex items-center gap-2">
								<input
									type="checkbox"
									checked={question.required}
									disabled={disabled}
									onChange={(event) => update({ required: event.target.checked })}
								/>
								必填
							</label>
							<Button
								type="button"
								variant="ghost"
								size="sm"
								disabled={disabled || config.questions.length <= 1}
								onClick={() =>
									onChange({
										...config,
										questions: config.questions.filter(
											(_, position) => position !== index,
										),
									})
								}
							>
								删除问题
							</Button>
						</div>
					</div>
				);
			})}
			<Button
				type="button"
				variant="outline"
				size="sm"
				disabled={disabled || config.questions.length >= 16}
				onClick={() =>
					onChange({
						...config,
						questions: [
							...config.questions,
							{
								key: `field_${Date.now()}`,
								label: '请补充所需信息',
								required: true,
								options: [],
							},
						],
					})
				}
			>
				添加问题
			</Button>
		</div>
	);
}
