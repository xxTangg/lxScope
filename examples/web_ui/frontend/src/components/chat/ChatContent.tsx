import { ReplyFinishedReason } from '@agentscope-ai/agentscope/event';
import {
	type ContentBlock,
	getContentBlocks,
	type Msg,
	type TextBlock,
	type ToolCallBlock,
} from '@agentscope-ai/agentscope/message';
import { GitBranch, TriangleAlert } from 'lucide-react';
import React, { useEffect, useMemo, useState } from 'react';

import { Button } from '../ui/button';
import { DiffStats } from './tool-renderers/_shared';
import type { GitStatus } from '@/api';
import { ASMessageBubble } from '@/components/chat/ASMessageBubble.tsx';
import { ConfirmCard } from '@/components/chat/ConfirmCard.tsx';
import { FlipCard } from '@/components/chat/FlipCard.tsx';
import { TextInput } from '@/components/chat/TextInput.tsx';
import { WorkingDirectoryDialog } from '@/components/dialog/WorkingDirectoryDialog';
import { Alert, AlertAction, AlertDescription, AlertTitle } from '@/components/ui/alert.tsx';
import { Badge } from '@/components/ui/badge';
import { Marker, MarkerContent } from '@/components/ui/marker';
import {
	MessageScroller,
	MessageScrollerButton,
	MessageScrollerContent,
	MessageScrollerItem,
	MessageScrollerProvider,
	MessageScrollerViewport,
} from '@/components/ui/message-scroller.tsx';
import { Spinner } from '@/components/ui/spinner';
import type { ReplyPhase } from '@/hooks/useMessages';
import { useTranslation } from '@/i18n/useI18n';
import { cn } from '@/lib/utils';

/** How long a load may run before it is worth showing a spinner. */
const SPINNER_DELAY_MS = 150;

/**
 * A gap this long between two messages gets a marker stamping when the
 * conversation resumed — long enough that it *was* resumed, rather than
 * merely paused to read the last reply.
 */
const TIME_MARKER_GAP_MS = 10 * 60 * 1000;

function isInternalTaskMessage(message: Msg): boolean {
	return (
		message.metadata?.scope === 'task' &&
		message.metadata?.visibility === 'internal'
	);
}

interface TaskSummary {
	key: string;
	taskId: string;
	runId: string;
	nodeCount: number;
	latestText: string;
	status: 'running' | 'succeeded' | 'failed';
	createdAt: string;
}

function taskMetadataText(message: Msg, key: string): string | null {
	const value = message.metadata?.[key];
	return typeof value === 'string' && value ? value : null;
}

function taskMessageText(message: Msg): string {
	return getContentBlocks(message, 'text')
		.map((block) => block.text)
		.join('\n')
		.trim();
}

function TaskSummaryCard({ summary }: { summary: TaskSummary }) {
	const statusLabel =
		summary.status === 'running'
			? '执行中'
			: summary.status === 'succeeded'
				? '已完成'
				: '执行失败';
	return (
		<div className="rounded-2xl border border-primary/15 bg-primary/[0.03] p-4">
			<div className="flex items-center gap-2">
				<div className="min-w-0 flex-1">
					<div className="text-sm font-medium">任务执行</div>
					<div className="mt-1 text-xs text-muted-foreground">
						已处理 {summary.nodeCount} 个 AI 节点 · Run {summary.runId.slice(0, 8)}
					</div>
				</div>
				<Badge
					className={
						summary.status === 'failed'
							? 'bg-red-100 text-red-700'
							: summary.status === 'running'
								? 'bg-amber-100 text-amber-700'
								: 'bg-emerald-100 text-emerald-700'
					}
				>
					{statusLabel}
				</Badge>
			</div>
			{summary.latestText && (
				<div className="mt-3 line-clamp-3 whitespace-pre-wrap text-sm leading-6 text-foreground/85">
					{summary.latestText}
				</div>
			)}
			<div className="mt-3 flex items-center justify-between gap-3 text-xs text-muted-foreground">
				<span>节点 Prompt 和完整输出已收纳到任务详情</span>
				<a className="shrink-0 hover:text-foreground" href={`/task/${summary.taskId}`}>
					查看任务详情
				</a>
			</div>
		</div>
	);
}

/**
 * Stamp for a resumed conversation. The time alone is enough while the
 * marker and the message above it share a day; once the gap crosses
 * midnight the date has to come with it. Both come from `Intl`, so
 * "Aug 13, 13:03" and "8月13日 13:03" fall out of the active language
 * rather than out of a hand-written pattern per locale.
 */
function markerStamp(at: Date, previous: Date, language: string): string {
	const sameDay = at.toDateString() === previous.toDateString();
	return new Intl.DateTimeFormat(language, {
		...(sameDay ? {} : { month: 'short', day: 'numeric' }),
		hour: '2-digit',
		minute: '2-digit',
		hour12: false,
	}).format(at);
}

interface ChatContentProps {
	msgs: Msg[];
	/**
	 * Whether the history for the currently selected session is still on
	 * the wire. Takes precedence over {@link msgs}, which still holds the
	 * *previous* session's messages until the fetch effect clears them.
	 */
	loading?: boolean;
	/**
	 * Reply lifecycle phase from ``useMessages`` — forwarded to
	 * ``TextInput`` so the single send / stop button can pick its
	 * icon, tooltip, disabled state and click handler from one source.
	 */
	phase: ReplyPhase;
	disabled: boolean;
	onSend: (content: ContentBlock[]) => void;
	onUserConfirm: (
		toolCall: ToolCallBlock,
		confirm: boolean,
		replyId: string,
		rules?: ToolCallBlock['suggested_rules'],
	) => Promise<void>;
	autoComplete?: (input: string) => string | null;
	className?: string;
	/** Called when the user clicks the stop button. */
	onInterrupt?: () => void;
	/**
	 * Optional content pinned at the bottom of the chat — between the
	 * message scroll area and the text input (e.g. pending subagent HITL
	 * cards on a team leader's view). Rendered below the conversation so
	 * a pending confirmation sits next to the input, where the user is
	 * looking, rather than scrolled off the top.
	 */
	footerSlot?: React.ReactNode;
	/** @see TextInputProps.allowedInputTypes */
	allowedInputTypes: string[];
	/** @see TextInputProps.fileProcessor */
	fileProcessor: (file: File) => Promise<ContentBlock | null>;
	/** Agent owning the open session — drives the directory picker. */
	agentId: string | null;
	/** The open session, whose working directory the picker edits. */
	sessionId: string | null;
	/** Current working directory, relative to the workspace root. */
	cwd: string | null;
	/** Persists a new working directory. */
	onCwdChange: (cwd: string | null) => void | Promise<void>;
	/** Git state of {@link cwd}; `null` hides the branch badge entirely. */
	git?: GitStatus | null;
	/** Re-reads the git state, since nothing polls for it. */
	onRefreshGit?: () => void | Promise<void>;
}

const ChatContentComponent: React.FC<ChatContentProps> = ({
	msgs,
	loading = false,
	phase,
	disabled,
	onSend,
	onUserConfirm,
	autoComplete,
	className,
	onInterrupt,
	footerSlot,
	allowedInputTypes,
	fileProcessor,
	agentId,
	sessionId,
	cwd,
	onCwdChange,
	git,
	onRefreshGit,
}) => {
	const { t, i18n } = useTranslation();
	const visibleMsgs = useMemo(
		() => msgs.filter((message) => !isInternalTaskMessage(message)),
		[msgs],
	);
	const taskSummaries = useMemo(() => {
		const grouped = new Map<string, TaskSummary>();
		for (const message of msgs) {
			if (!isInternalTaskMessage(message)) continue;
			const taskId = taskMetadataText(message, 'task_id');
			const runId = taskMetadataText(message, 'run_id');
			if (!taskId || !runId) continue;
			const key = `${taskId}:${runId}`;
			const current = grouped.get(key) ?? {
				key,
				taskId,
				runId,
				nodeCount: 0,
				latestText: '',
				status: 'running',
				createdAt: message.created_at,
			};
			if (message.role === 'assistant') {
				if (taskMetadataText(message, 'node_id')) current.nodeCount += 1;
				current.latestText = taskMessageText(message) || current.latestText;
				current.status = message.error ? 'failed' : message.finished_at ? 'succeeded' : 'running';
			}
			grouped.set(key, current);
		}
		return [...grouped.values()].sort(
			(a, b) => new Date(a.createdAt).getTime() - new Date(b.createdAt).getTime(),
		);
	}, [msgs]);
	// Only a session that finished loading with nothing in it is empty.
	// Treating "no messages yet" as empty would flash the greeting over
	// every session that does have history.
	const isEmpty = !loading && visibleMsgs.length === 0 && taskSummaries.length === 0;

	// A spinner that appears and vanishes inside a couple of frames reads
	// as a flicker, not as feedback — so hold it back until the load has
	// gone on long enough to be worth reporting. The gap renders blank,
	// which is invisible at this duration.
	const [showSpinner, setShowSpinner] = useState(false);
	useEffect(() => {
		if (!loading) {
			setShowSpinner(false);
			return;
		}
		const timer = setTimeout(() => setShowSpinner(true), SPINNER_DELAY_MS);
		return () => clearTimeout(timer);
	}, [loading]);

	const toConfirmedToolCalls = useMemo(() => {
		if (visibleMsgs.length === 0) return [];

		const lastMsg = visibleMsgs[visibleMsgs.length - 1];
		return getContentBlocks(lastMsg, 'tool_call')
			.filter((tc) => tc.state === 'asking')
			.map((tc) => ({ replyId: lastMsg.id, toolCall: tc }));
	}, [visibleMsgs]);

	// REPLY_START can arrive noticeably later than the accepted chat
	// request. Keep feedback in the message flow during that interval;
	// once an assistant message appears, its own running footer takes over.
	const waitingForReply =
		phase !== 'idle' &&
		visibleMsgs.length > 0 &&
		visibleMsgs[visibleMsgs.length - 1].role === 'user';

	// On an empty session the prompt and the input centre together, so every box
	// down to the message list shrinks to its content instead of filling.
	return (
		<div
			className={cn(
				'flex flex-col h-full w-full items-center gap-4',
				isEmpty && 'justify-center',
				className,
			)}
		>
			{loading ? (
				<div className="flex flex-1 w-full items-center justify-center">
					{showSpinner ? <Spinner className="size-5 text-muted-foreground" /> : null}
				</div>
			) : isEmpty ? (
				<span className="text-center text-4xl font-light tracking-[-0.035em] text-foreground mb-2">
					{t('chat.greeting')}
				</span>
			) : (
				<MessageScrollerProvider autoScroll={true} defaultScrollPosition={'end'}>
					<MessageScroller>
						<MessageScrollerViewport>
							<MessageScrollerContent>
								{taskSummaries.map((summary) => (
									<MessageScrollerItem key={summary.key} messageId={summary.key}>
										<TaskSummaryCard summary={summary} />
									</MessageScrollerItem>
								))}
								{visibleMsgs.map((message, index) => {
									const previous = visibleMsgs[index - 1];
									const at = new Date(message.created_at);
									const previousAt = previous
										? new Date(previous.finished_at ?? previous.created_at)
										: at;
									return (
										<MessageScrollerItem
											key={message.id}
											messageId={message.id}
										>
											{at.getTime() - previousAt.getTime() >
												TIME_MARKER_GAP_MS && (
												<Marker
													variant="separator"
													className="mb-6 font-mono text-xs"
												>
													<MarkerContent>
														{markerStamp(at, previousAt, i18n.language)}
													</MarkerContent>
												</Marker>
											)}
											<ASMessageBubble
												message={message}
												agentId={agentId}
												sessionId={sessionId}
												onUserConfirm={onUserConfirm}
											/>
										</MessageScrollerItem>
									);
								})}
								{waitingForReply && (
									<MessageScrollerItem
										key="reply-pending"
										messageId="reply-pending"
									>
										<div
											role="status"
											aria-live="polite"
											className="flex items-center gap-2 py-2 text-sm text-muted-foreground"
										>
											<Spinner className="size-4" />
											<span>
												{phase === 'interrupting'
													? t('textInput.stopping')
													: t('chat.replyPending')}
											</span>
										</div>
									</MessageScrollerItem>
								)}
								{visibleMsgs.length > 0 &&
									visibleMsgs[visibleMsgs.length - 1].finished_reason ===
										ReplyFinishedReason.EXCEED_MAX_ITERS &&
									phase === 'idle' && (
										<Alert
											variant={'default'}
											className="border-amber-200 bg-amber-50 text-amber-900 dark:border-amber-900 dark:bg-amber-950 dark:text-amber-50"
										>
											<TriangleAlert />
											<AlertTitle>
												{t('chat.maxItersExceeded.title')}
											</AlertTitle>
											<AlertDescription>
												{t('chat.maxItersExceeded.description')}
											</AlertDescription>
											<AlertAction>
												<Button
													size={'xs'}
													onClick={() => {
														onSend([
															{
																id: crypto.randomUUID(),
																type: 'text',
																text: t(
																	'chat.maxItersExceeded.continue',
																),
																created_at:
																	new Date().toISOString(),
																finished_at:
																	new Date().toISOString(),
															} as TextBlock,
														]);
													}}
												>
													{t('chat.maxItersExceeded.continue')}
												</Button>
											</AlertAction>
										</Alert>
									)}
							</MessageScrollerContent>
						</MessageScrollerViewport>
						<MessageScrollerButton className="rounded-full" />
					</MessageScroller>
				</MessageScrollerProvider>
			)}

			{loading ? null : (
				<div className="relative min-w-full max-w-full w-full">
					<FlipCard
						visible={toConfirmedToolCalls.length > 0 || footerSlot !== null}
						className="absolute bottom-full left-0 right-0 mb-2 z-50"
					>
						{toConfirmedToolCalls.length > 0 ? (
							<ConfirmCard
								key={`${toConfirmedToolCalls[0].replyId}:${toConfirmedToolCalls[0].toolCall.id}`}
								toolCall={toConfirmedToolCalls[0].toolCall}
								onUserConfirm={(confirm, rules) =>
									onUserConfirm(
										toConfirmedToolCalls[0].toolCall,
										confirm,
										toConfirmedToolCalls[0].replyId,
										rules,
									)
								}
							/>
						) : (
							footerSlot
						)}
					</FlipCard>
					{/* Concentric radii: the pill stays rounded-[28px] on all four
				    corners and floats on this surface, whose own radius is
				    28 + the 4px padding. Matching them is what keeps the two
				    edges parallel — a plain rectangle here would show the
				    pill's corners cutting into it. */}
					<TextInput
						className="min-w-full max-w-full w-full rounded-[32px] bg-muted p-1"
						onSend={onSend}
						disabled={disabled}
						autoComplete={autoComplete}
						allowedInputTypes={allowedInputTypes}
						fileProcessor={fileProcessor}
						phase={phase}
						onInterrupt={onInterrupt}
						headerSlot={
							<div className="flex w-full items-center justify-between px-2 py-1 text-sm text-muted-foreground">
								<WorkingDirectoryDialog
									agentId={agentId}
									sessionId={sessionId}
									value={cwd}
									onChange={onCwdChange}
								/>
								{git && (
									<Button
										className="font-mono"
										variant="secondary"
										size="sm"
										onClick={() => void onRefreshGit?.()}
										title={t('workdir.gitTooltip', {
											staged: git.staged,
											unstaged: git.unstaged,
											untracked: git.untracked,
										})}
									>
										<GitBranch />
										{/* A detached HEAD has no branch to name, so
										    fall back to the commit it sits on. The
										    server sends no git at all when it has
										    neither. */}
										{git.branch ?? git.head?.slice(0, 7)}
										{git.ahead !== null && git.ahead > 0 && (
											<span className="text-xs">↑{git.ahead}</span>
										)}
										{git.behind !== null && git.behind > 0 && (
											<span className="text-xs">↓{git.behind}</span>
										)}
										{/* Renders nothing when both are zero, so a
										    clean tree shows just the branch. */}
										<DiffStats
											className="text-xs font-mono"
											insertions={git.insertions}
											deletions={git.deletions}
										/>
									</Button>
								)}
							</div>
						}
					/>
				</div>
			)}
		</div>
	);
};

export const ChatContent = React.memo(ChatContentComponent);
