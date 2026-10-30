import { afterEach, expect, it, vi } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { WorkViews } from './WorkViews';
import { Api } from './api/client';
import type { Snapshot } from './App';

const empty = (): Snapshot => ({ tasks: [], runs: [], approvals: [], receipts: [], memory: [], orders: [], schedules: [], goal: null, integrations: {} });
afterEach(cleanup);
it('names the external destination before a human approves a Bakeoff change', async () => {
  const api = new Api('bakery');
  const request = vi.spyOn(api, 'request').mockResolvedValue({ status: 'APPROVED' });
  const data = empty();
  data.approvals = [{ id: 'approval-007', owner_id: 'synthetic-dani', domain: 'bakery', synthetic: true, created_at: '2026-10-28T00:00:00Z', updated_at: '2026-10-28T00:00:00Z', purpose: 'task hub: report the synthetic equipment issue', status: 'PENDING', expires_at: '2026-10-29T00:00:00Z', execution_authorized: false, effect: 'HOST_BAKEOFF' }];
  const notices: string[] = [];
  render(<WorkViews page="Approvals" api={api} data={data} busy={false} close={() => {}} act={async (work, notice) => { await work(); notices.push(notice); }}/>);
  expect(screen.queryByRole('button', { name: 'Approve local action' })).not.toBeInTheDocument();
  await userEvent.click(screen.getByRole('button', { name: 'Approve task hub change' }));
  expect(request).toHaveBeenCalledWith('/approvals/approval-007/decision', 'POST', { decision: 'APPROVED' });
  expect(notices[0]).toContain('task hub');
  expect(notices[0]).not.toContain('disabled');
});
it('keeps unknown outcomes visible for human review without a cancel action', () => {
  const data = empty();
  data.runs = [{ id: 'run-007', owner_id: 'synthetic-dani', domain: 'bakery', synthetic: true, created_at: '2026-10-28T00:00:00Z', updated_at: '2026-10-28T00:00:00Z', tool: 'bakeoff.create_incident', arguments: {}, status: 'UNRESOLVED', checkpoint: {}, attempts: 1, max_attempts: 3, next_attempt_at: '2026-10-28T00:00:00Z', lease_until: null, error_code: 'BAKEOFF_TIMEOUT' }];
  render(<WorkViews page="Working for You" api={new Api('bakery')} data={data} busy={false} close={() => {}} act={async () => {}}/>);
  expect(screen.getByText('Outcome unknown. Review task hub before attempting this change again.')).toBeInTheDocument();
  expect(screen.queryByRole('button', { name: 'Cancel' })).not.toBeInTheDocument();
});
