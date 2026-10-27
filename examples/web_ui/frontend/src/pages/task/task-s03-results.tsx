import {
	ArrowLeft,
	Building2,
	CircleAlert,
	FileText,
	Lightbulb,
	ShieldAlert,
	UsersRound,
} from 'lucide-react';
import * as React from 'react';

import { TASK_RESULT_CARD_CLASS } from './task-result-card-style';
import { adaptS03ExpertMatchingRun } from './task-s03-result-adapter';
import type { S03RiskItem } from './task-s03-result-adapter';
import type { TaskRunRecord } from '@/api';
import { Markdown } from '@/components/markdown';
import { Button } from '@/components/ui/button';

interface TaskS03ResultsProps {
	run: TaskRunRecord;
	onViewExecution: () => void;
	onViewFullResult: () => void;
}

function ResultCard({
	icon,
	title,
	children,
}: {
	icon: React.ReactNode;
	title: string;
	children: React.ReactNode;
}) {
	return (
		<section className={`flex min-h-[270px] min-w-0 flex-col rounded-2xl border border-slate-200 bg-white p-4 lg:h-[330px] lg:min-h-[330px] ${TASK_RESULT_CARD_CLASS}`}>
			<header className="mb-3 flex h-8 shrink-0 items-center gap-2.5">
				<span className="flex size-8 shrink-0 items-center justify-center rounded-xl bg-blue-50 text-blue-600">{icon}</span>
				<h2 className="min-w-0 truncate text-sm font-semibold text-slate-900">{title}</h2>
			</header>
			<div className="min-h-0 flex-1">{children}</div>
		</section>
	);
}

function EmptyNote({ children }: { children: React.ReactNode }) {
	return <p className="rounded-xl bg-slate-50 px-3 py-2.5 text-xs leading-5 text-slate-500">{children}</p>;
}

function RiskTone({ tone }: { tone: S03RiskItem['tone'] }) {
	return tone === 'danger' ? (
		<CircleAlert className="mt-0.5 size-4 shrink-0 text-red-600" aria-hidden="true" />
	) : (
		<CircleAlert className="mt-0.5 size-4 shrink-0 text-amber-600" aria-hidden="true" />
	);
}

function formatScore(score: number): string {
	return Number.isInteger(score) ? String(score) : score.toFixed(2).replace(/0+$/, '').replace(/\.$/, '');
}

export function TaskS03Results({ run, onViewExecution, onViewFullResult }: TaskS03ResultsProps) {
	const result = React.useMemo(() => adaptS03ExpertMatchingRun(run), [run]);
	const modules = [result.hasDemandData, result.hasRecommendationData, result.hasAnalysisData, result.hasRiskData];
	const hasStructuredContent = modules.some(Boolean);
	const needsMarkdownSupplement = modules.some((hasData) => !hasData) && Boolean(result.markdown);

	return (
		<div className="mx-auto w-full max-w-[1500px] space-y-4 pb-3">
			<header className="flex flex-wrap items-start justify-between gap-3">
				<div>
					<h1 className="text-xl font-semibold tracking-tight text-slate-950">专家智能匹配成果</h1>
					<p className="mt-1 text-xs text-slate-500">项目需求画像 · 专家初步推荐 · 匹配依据 · 人工复核事项</p>
				</div>
				<Button type="button" variant="outline" size="sm" onClick={onViewExecution}>
					<ArrowLeft />
					返回执行过程
				</Button>
			</header>

			{hasStructuredContent ? (
				<div className="grid min-w-0 gap-4 lg:grid-cols-2">
					<ResultCard
						icon={<Building2 className="size-4" aria-hidden="true" />}
						title="项目需求画像"
					>
						{result.demandFields.length ? (
							<dl className="grid grid-cols-1 gap-x-4 gap-y-2 sm:grid-cols-2">
								{result.demandFields.map((field) => (
									<div key={field.label} className={field.label.includes('项目背景') ? 'min-w-0 sm:col-span-2' : 'min-w-0'}>
										<dt className="text-[10px] text-slate-500">{field.label}</dt>
										{field.label === '所需专家方向' ? (
											<div className="mt-1 flex flex-wrap gap-1">
												{field.value.split('、').map((item) => <span key={item} className="max-w-full truncate rounded-full border border-blue-100 bg-blue-50/70 px-2 py-0.5 text-[10px] text-blue-800">{item}</span>)}
											</div>
										) : (
											<dd className="mt-0.5 line-clamp-2 break-words text-xs leading-5 text-slate-800">{field.value}</dd>
										)}
									</div>
								))}
							</dl>
						) : <EmptyNote>本次结果未提取到可识别的项目需求信息。</EmptyNote>}
					</ResultCard>

					<ResultCard
						icon={<UsersRound className="size-4" aria-hidden="true" />}
						title="专家推荐 TOP N"
					>
						{result.recommendations.length ? (
							<ol className="space-y-2">
								{result.recommendations.map((candidate) => (
									<li key={candidate.id} className="flex min-w-0 gap-2.5 rounded-xl border border-slate-100 bg-slate-50/60 px-2.5 py-2">
										<span className="flex size-6 shrink-0 items-center justify-center rounded-lg bg-white text-[10px] font-semibold text-blue-700 shadow-sm">{candidate.rank ?? '–'}</span>
										<div className="min-w-0 flex-1">
											<div className="flex min-w-0 items-start justify-between gap-2">
												<div className="min-w-0">
													<p className="truncate text-xs font-semibold text-slate-900">{candidate.name}</p>
													<p className="mt-0.5 text-[10px] font-medium text-blue-700">初步推荐 · 用户声明可参与初排</p>
												</div>
												{candidate.score !== null && <span className="shrink-0 rounded-md bg-white px-1.5 py-0.5 text-[10px] font-medium text-slate-600">{formatScore(candidate.score)} 分</span>}
											</div>
											{candidate.professionalFields.length > 0 && <p className="mt-1 line-clamp-1 text-[10px] leading-4 text-slate-500">专业方向：{candidate.professionalFields.join('、')}</p>}
											{candidate.reason && <p className="mt-0.5 line-clamp-2 text-[11px] leading-4 text-slate-700">{candidate.reason}</p>}
										</div>
									</li>
								))}
							</ol>
						) : <EmptyNote>本次结果暂无可展示的专家推荐名单，详情请查看完整结果。</EmptyNote>}
					</ResultCard>

					<ResultCard
						icon={<Lightbulb className="size-4" aria-hidden="true" />}
						title="智能匹配分析"
					>
						{result.analysis.length ? (
							<ul className="space-y-3">
								{result.analysis.map((item, index) => (
									<li key={`${item.title}-${index}`} className="flex gap-2.5">
										<span className="mt-1.5 size-1.5 shrink-0 rounded-full bg-blue-500" />
										<div className="min-w-0">
											<p className="text-[11px] font-semibold text-slate-800">{item.title}</p>
											<p className="mt-0.5 line-clamp-3 text-xs leading-5 text-slate-600">{item.detail}</p>
										</div>
									</li>
								))}
							</ul>
						) : <EmptyNote>本次结果未提取到可确认的专业匹配分析。</EmptyNote>}
					</ResultCard>

					<ResultCard
						icon={<ShieldAlert className="size-4" aria-hidden="true" />}
						title="回避风险与人工复核"
					>
						<ul className="space-y-2">
							{result.risks.map((item, index) => (
								<li key={`${item.title}-${index}`} className={`flex gap-2 rounded-xl border px-2.5 py-2 ${item.tone === 'danger' ? 'border-red-100 bg-red-50/60' : 'border-amber-100 bg-amber-50/50'}`}>
									<RiskTone tone={item.tone} />
									<div className="min-w-0">
										<p className={`text-[11px] font-semibold ${item.tone === 'danger' ? 'text-red-800' : 'text-amber-900'}`}>{item.title}</p>
										<p className="mt-0.5 line-clamp-2 text-[10px] leading-4 text-slate-600">{item.detail}</p>
									</div>
								</li>
							))}
						</ul>
					</ResultCard>
				</div>
			) : (
				<section className="rounded-2xl border border-slate-200 bg-white p-4 shadow-sm">
					<div className="mb-3 flex items-center gap-2.5">
						<span className="flex size-8 items-center justify-center rounded-xl bg-blue-50 text-blue-600"><FileText className="size-4" aria-hidden="true" /></span>
						<h2 className="text-sm font-semibold text-slate-900">专家匹配完整结果</h2>
					</div>
					{result.markdown ? (
						<Markdown animated={false} className="prose prose-slate prose-sm max-w-none break-words leading-5 prose-headings:my-3 prose-headings:text-sm prose-p:my-2 prose-p:text-xs prose-li:my-1 prose-li:text-xs prose-th:text-xs prose-td:text-xs prose-pre:overflow-x-auto prose-pre:rounded-lg prose-table:block prose-table:max-w-full prose-table:overflow-x-auto">
							{result.markdown}
						</Markdown>
					) : <EmptyNote>本次运行没有可读取的 Markdown 结果，请从原始结果或成果文件中查看。</EmptyNote>}
				</section>
			)}

			{hasStructuredContent && needsMarkdownSupplement && result.markdown && (
				<details className="rounded-2xl border border-slate-200 bg-white px-4 py-3">
					<summary className="cursor-pointer text-xs font-medium text-slate-700">展开未结构化的 Markdown 完整结果</summary>
					<div className="mt-3 border-t border-slate-100 pt-3">
						<Markdown animated={false} className="prose prose-slate prose-sm max-w-none break-words leading-5 prose-headings:my-3 prose-headings:text-sm prose-p:my-2 prose-p:text-xs prose-li:my-1 prose-li:text-xs prose-th:text-xs prose-td:text-xs prose-pre:overflow-x-auto prose-pre:rounded-lg prose-table:block prose-table:max-w-full prose-table:overflow-x-auto">
							{result.markdown}
						</Markdown>
					</div>
				</details>
			)}

			<section className="rounded-2xl border border-slate-200 bg-slate-50/70 p-4">
				<div className="flex flex-wrap items-center justify-between gap-3">
					<div className="min-w-0">
						<div className="flex items-center gap-2 text-sm font-semibold text-slate-900"><FileText className="size-4 text-blue-600" aria-hidden="true" />成果文件与完整结果</div>
						<p className="mt-1 text-xs leading-5 text-slate-500">以上为本次专家智能匹配的关键成果摘要，完整专家信息、推荐理由及核查事项请查看任务成果文件。</p>
					</div>
					<Button type="button" variant="outline" size="sm" onClick={onViewFullResult}>
						<FileText />
						{result.artifacts.length ? '打开预览与下载' : '查看原始结果'}
					</Button>
				</div>
				{result.artifacts.length ? (
					<ul className="mt-3 grid gap-2 sm:grid-cols-2">
						{result.artifacts.map((artifact) => (
							<li key={artifact.id} className="flex min-w-0 items-center gap-2 rounded-xl border border-slate-200 bg-white px-3 py-2">
								<FileText className="size-4 shrink-0 text-slate-400" aria-hidden="true" />
								<span className="min-w-0 flex-1 truncate text-xs font-medium text-slate-700" title={artifact.name}>{artifact.name}</span>
								<span className="shrink-0 rounded-md bg-slate-100 px-1.5 py-0.5 text-[10px] font-medium text-slate-500">{artifact.format.toUpperCase()}</span>
							</li>
						))}
					</ul>
				) : <p className="mt-3 rounded-xl border border-dashed border-slate-300 bg-white px-3 py-2.5 text-xs text-slate-500">本次运行未生成成果文件，仍可查看 TaskRun 原始结果。</p>}
			</section>
		</div>
	);
}
