import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import type { ComponentProps } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import i18n from '../src/i18n';
import { LoginCaptchaField } from '../src/pages/login/LoginCaptchaField';

const imageDataUrl = 'data:image/png;base64,aW1hZ2U=';

function renderCaptcha(overrides: Partial<ComponentProps<typeof LoginCaptchaField>> = {}) {
	const onChange = vi.fn();
	const onRefresh = vi.fn();
	render(
		<LoginCaptchaField
			value=""
			imageDataUrl={imageDataUrl}
			expiresIn={300}
			loading={false}
			submitting={false}
			onChange={onChange}
			onRefresh={onRefresh}
			{...overrides}
		/>,
	);
	return { onChange, onRefresh };
}

describe('LoginCaptchaField', () => {
	beforeEach(async () => {
		await i18n.changeLanguage('zh');
	});

	afterEach(() => cleanup());

	it('shows the labeled five-character input, image and expiry hint', () => {
		renderCaptcha();

		const input = screen.getByRole('textbox', { name: '验证码' });
		const image = screen.getByRole('img', { name: /图形验证码/ });

		expect(input.getAttribute('autocomplete')).toBe('one-time-code');
		expect(input.getAttribute('maxlength')).toBe('5');
		expect(input.hasAttribute('required')).toBe(true);
		expect(image.getAttribute('src')).toBe(imageDataUrl);
		expect(screen.getByText('5位字符，300 秒内有效。')).toBeTruthy();
	});

	it('normalizes input and refreshes from both the image and refresh button', () => {
		const { onChange, onRefresh } = renderCaptcha();
		fireEvent.change(screen.getByRole('textbox', { name: '验证码' }), {
			target: { value: 'ab234xyz' },
		});
		fireEvent.click(screen.getByRole('button', { name: '点击图片刷新验证码' }));
		fireEvent.click(screen.getByRole('button', { name: '刷新验证码' }));

		expect(onChange).toHaveBeenCalledWith('AB234');
		expect(onRefresh).toHaveBeenCalledTimes(2);
	});

	it('disables interaction while loading and shows service errors inline', () => {
		renderCaptcha({ loading: true, error: '验证码已过期，请刷新后重试。' });

		expect((screen.getByRole('textbox', { name: '验证码' }) as HTMLInputElement).disabled).toBe(true);
		expect((screen.getByRole('button', { name: '点击图片刷新验证码' }) as HTMLButtonElement).disabled).toBe(true);
		expect(screen.getByText('验证码已过期，请刷新后重试。')).toBeTruthy();
	});
});
