import { afterEach, expect, it } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import App from './App';

afterEach(cleanup);
it('requires explicit entry to the synthetic workspace', () => {
  render(<App/>);
  expect(screen.getByRole('button', { name: /Open synthetic workspace/ })).toBeInTheDocument();
  expect(screen.getByText('This development session does not sign you in as Dani.')).toBeInTheDocument();
  expect(screen.queryByRole('button', { name: /Add task/ })).not.toBeInTheDocument();
});
it('provides keyboard-operable navigation with one current page', async () => {
  render(<App/>);
  await userEvent.click(screen.getByRole('button', { name: /My Day/ }));
  expect(screen.getByRole('heading', { level: 1, name: 'My Day' })).toBeInTheDocument();
  expect(screen.getByRole('button', { name: /My Day/ })).toHaveAttribute('aria-current', 'page');
  expect(screen.getByRole('link', { name: 'Skip to content' })).toHaveAttribute('href', '#main');
});