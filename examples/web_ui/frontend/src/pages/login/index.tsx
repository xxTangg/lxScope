import { ArrowLeft, CircleAlert, Loader2 } from 'lucide-react';
import { useEffect, useRef, useState } from 'react';
import { Navigate, useLocation, useNavigate } from 'react-router-dom';

import { ApiError, TIMEOUT_STATUS } from '@/api/client';
import AgentScope from '@/assets/images/agentscope.svg?react';
import { Alert, AlertDescription } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Field, FieldGroup, FieldLabel } from '@/components/ui/field';
import { Input } from '@/components/ui/input';
import { useAuth } from '@/hooks/useAuth';
import { useTranslation } from '@/i18n/useI18n';
import { formatApiErrorForAlert } from '@/lib/api-error';

export function LoginPage() {
	const { t } = useTranslation();
	const {
		status,
		login,
		register,
		logtoEnabled,
		organizations,
		organizationNames,
		beginLogin,
		selectTenant,
		switchAccount,
		logout,
		error: authError,
	} = useAuth();
	const navigate = useNavigate();
	const location = useLocation();
	const [username, setUsername] = useState('');
	const [password, setPassword] = useState('');
	const [confirmPassword, setConfirmPassword] = useState('');
	const [mode, setMode] = useState<'login' | 'register'>('login');
	const [submitting, setSubmitting] = useState(false);
	const [error, setError] = useState('');
	const [tenantId, setTenantId] = useState('');
	const autoLoginStarted = useRef(false);
	const autoTenantAttempted = useRef('');

	useEffect(() => {
		if (organizations.includes(tenantId)) return;
		const preferred = localStorage.getItem('lxscope_active_tenant');
		setTenantId(
			(preferred && organizations.includes(preferred) ? preferred : organizations[0]) ?? '',
		);
	}, [organizations, tenantId]);

	useEffect(() => {
		if (
			!logtoEnabled ||
			status !== 'anonymous' ||
			autoLoginStarted.current
		) {
			return;
		}
		autoLoginStarted.current = true;
		void beginLogin().catch((reason) => {
			setError(reason instanceof Error ? reason.message : 'Logto 登录失败。');
		});
	}, [beginLogin, logtoEnabled, status]);

	useEffect(() => {
		if (
			!logtoEnabled ||
			status !== 'selecting_tenant' ||
			organizations.length !== 1
		) {
			return;
		}
		const onlyOrganization = organizations[0];
		if (autoTenantAttempted.current === onlyOrganization) return;
		autoTenantAttempted.current = onlyOrganization;
		setSubmitting(true);
		void selectTenant(onlyOrganization)
			.then(() => {
				const from = (location.state as { from?: string } | null)?.from;
				navigate(from || '/chat', { replace: true });
			})
			.catch((reason) => {
				setError(reason instanceof Error ? reason.message : '进入组织失败。');
			})
			.finally(() => setSubmitting(false));
	}, [location.state, logtoEnabled, navigate, organizations, selectTenant, status]);

	if (status === 'authenticated') return <Navigate to="/chat" replace />;
	if (status === 'loading') {
		return (
			<div className="flex h-screen items-center justify-center bg-canvas">
				<Loader2 className="size-5 animate-spin text-muted-foreground" />
			</div>
		);
	}
	if (
		logtoEnabled &&
		status === 'anonymous' &&
		!error &&
		!authError
	) {
		return (
			<div className="flex h-screen items-center justify-center bg-canvas">
				<Loader2 className="size-5 animate-spin text-muted-foreground" />
			</div>
		);
	}

	if (logtoEnabled) {
		const chooseTenant = async (event: React.FormEvent) => {
			event.preventDefault();
			if (!tenantId) return;
			setSubmitting(true);
			setError('');
			try {
				await selectTenant(tenantId);
				const from = (location.state as { from?: string } | null)?.from;
				navigate(from || '/chat', { replace: true });
			} catch (reason) {
				setError(reason instanceof Error ? reason.message : '进入组织失败。');
			} finally {
				setSubmitting(false);
			}
		};

		const startLogtoLogin = async () => {
			setSubmitting(true);
			setError('');
			try {
				await beginLogin();
			} catch (reason) {
				setError(reason instanceof Error ? reason.message : 'Logto 登录失败。');
				setSubmitting(false);
			}
		};

		const returnToLogtoLogin = async () => {
			setSubmitting(true);
			setError('');
			try {
				await switchAccount();
			} catch (reason) {
				setError(reason instanceof Error ? reason.message : 'Logto 登录失败。');
				setSubmitting(false);
			}
		};

		return (
			<div className="flex h-screen items-center justify-center bg-canvas px-4">
				<div className="flex w-full max-w-sm flex-col gap-6">
					<div className="flex items-center justify-center gap-2.5">
						<AgentScope className="size-9" />
						<span className="text-lg font-semibold text-foreground">{t('auth.brand')}</span>
					</div>
					<Card>
						<CardHeader>
							<CardTitle>{status === 'selecting_tenant' ? '选择组织' : t('auth.title')}</CardTitle>
							<CardDescription>
								{status === 'selecting_tenant'
									? '选择要进入的组织；成员和管理员权限由 Logto 组织角色决定。'
									: t('auth.description')}
							</CardDescription>
						</CardHeader>
						<CardContent className="space-y-4">
							{status === 'selecting_tenant' ? (
								organizations.length === 0 ? (
									<div className="space-y-4">
										{(error || authError) && (
											<Alert variant="destructive"><CircleAlert /><AlertDescription>{error || authError}</AlertDescription></Alert>
										)}
										<Button type="button" variant="outline" className="w-full" onClick={() => void logout()}>
											退出 Logto
										</Button>
									</div>
								) : (
								<form onSubmit={chooseTenant} className="space-y-4">
									<Field>
										<FieldLabel htmlFor="logto-tenant">组织</FieldLabel>
										<select
											id="logto-tenant"
											className="border-input bg-background h-9 w-full rounded-md border px-3 text-sm"
											value={tenantId}
											onChange={(event) => setTenantId(event.target.value)}
											disabled={submitting || organizations.length === 0}
										>
							{organizations.map((id) => <option key={id} value={id}>{organizationNames[id] || id}</option>)}
										</select>
									</Field>
									{(error || authError) && (
										<Alert variant="destructive"><CircleAlert /><AlertDescription>{error || authError}</AlertDescription></Alert>
									)}
									<Button type="submit" className="w-full" disabled={submitting || !tenantId}>
										{submitting && <Loader2 className="size-3.5 animate-spin" />}
										进入组织
									</Button>
									<Button
										type="button"
										variant="outline"
										className="w-full"
										onClick={() => void returnToLogtoLogin()}
										disabled={submitting}
									>
										{submitting ? (
											<Loader2 className="size-3.5 animate-spin" />
										) : (
											<ArrowLeft className="size-3.5" />
										)}
										返回 Logto 登录
									</Button>
								</form>
								)
							) : (
								<div className="space-y-4">
									{(error || authError) && (
										<Alert variant="destructive"><CircleAlert /><AlertDescription>{error || authError}</AlertDescription></Alert>
									)}
									<Button className="w-full" onClick={() => void startLogtoLogin()} disabled={submitting}>
										{submitting && <Loader2 className="size-3.5 animate-spin" />}
										{submitting ? t('auth.signingIn') : '使用 Logto 登录'}
									</Button>
								</div>
							)}
						</CardContent>
					</Card>
				</div>
			</div>
		);
	}

	const describeError = (reason: unknown) => {
		if (reason instanceof ApiError) {
			if (reason.status === 401) return t('auth.invalidCredentials');
			if (reason.status === 409) return t('auth.usernameTaken');
			if (reason.status === 0) return t('auth.unreachable');
			if (reason.status === TIMEOUT_STATUS) return t('auth.timeout');
		}
		return formatApiErrorForAlert(reason);
	};

	const handleSubmit = async (event: React.FormEvent) => {
		event.preventDefault();
		if (mode === 'register' && password !== confirmPassword) {
			setError(t('auth.passwordMismatch'));
			return;
		}
		setSubmitting(true);
		setError('');
		try {
			if (mode === 'register') {
				await register(username.trim(), password);
			} else {
				await login(username.trim(), password);
			}
			const from = (location.state as { from?: string } | null)?.from;
			navigate(from || '/chat', { replace: true });
		} catch (reason) {
			setError(describeError(reason));
		} finally {
			setSubmitting(false);
		}
	};

	const switchMode = () => {
		setMode((current) => (current === 'login' ? 'register' : 'login'));
		setPassword('');
		setConfirmPassword('');
		setError('');
	};

	return (
		<div className="flex h-screen items-center justify-center bg-canvas px-4">
			<div className="flex w-full max-w-sm flex-col gap-6">
				<div className="flex items-center justify-center gap-2.5">
					<AgentScope className="size-9" />
					<span className="text-lg font-semibold text-foreground">{t('auth.brand')}</span>
				</div>
				<Card>
					<CardHeader>
						<CardTitle>
							{mode === 'register' ? t('auth.registerTitle') : t('auth.title')}
						</CardTitle>
						<CardDescription>
							{mode === 'register'
								? t('auth.registerDescription')
								: t('auth.description')}
						</CardDescription>
					</CardHeader>
					<CardContent>
						<form onSubmit={handleSubmit}>
							<FieldGroup>
								<Field>
									<FieldLabel htmlFor="login-username">
										{t('auth.username')}
									</FieldLabel>
									<Input
										id="login-username"
										type="text"
										autoComplete="username"
										value={username}
										onChange={(event) => setUsername(event.target.value)}
										placeholder={t('auth.usernamePlaceholder')}
										disabled={submitting}
										required
										autoFocus
									/>
								</Field>
								<Field>
									<FieldLabel htmlFor="login-password">
										{t('auth.password')}
									</FieldLabel>
									<Input
										id="login-password"
										type="password"
										autoComplete={
											mode === 'register'
												? 'new-password'
												: 'current-password'
										}
										value={password}
										onChange={(event) => setPassword(event.target.value)}
										placeholder={t('auth.passwordPlaceholder')}
										minLength={mode === 'register' ? 6 : undefined}
										disabled={submitting}
										required
									/>
								</Field>
								{mode === 'register' && (
									<Field>
										<FieldLabel htmlFor="login-confirm-password">
											{t('auth.confirmPassword')}
										</FieldLabel>
										<Input
											id="login-confirm-password"
											type="password"
											autoComplete="new-password"
											value={confirmPassword}
											onChange={(event) =>
												setConfirmPassword(event.target.value)
											}
											placeholder={t('auth.confirmPasswordPlaceholder')}
											disabled={submitting}
											minLength={6}
											required
										/>
									</Field>
								)}
								{error && (
									<Alert variant="destructive">
										<CircleAlert />
										<AlertDescription>{error}</AlertDescription>
									</Alert>
								)}
								<Button type="submit" className="w-full" disabled={submitting}>
									{submitting && <Loader2 className="size-3.5 animate-spin" />}
									{submitting
										? mode === 'register'
											? t('auth.registering')
											: t('auth.signingIn')
										: mode === 'register'
											? t('auth.register')
											: t('auth.signIn')}
								</Button>
								<Button
									type="button"
									variant="ghost"
									className="w-full"
									onClick={switchMode}
									disabled={submitting}
								>
									{mode === 'register'
										? t('auth.haveAccount')
										: t('auth.createAccount')}
								</Button>
							</FieldGroup>
						</form>
					</CardContent>
				</Card>
			</div>
		</div>
	);
}
