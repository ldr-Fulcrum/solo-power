import { useEffect, useMemo, useState } from 'react';
import { Api, type Approval, type Context, type Goal, type Memory, type Order, type Receipt, type Run, type Schedule, type Task } from './api/client';
import { Field, Empty, Status, submit, money, type Action } from './ui';
import { WorkViews } from './WorkViews';
import { BakeryView } from './BakeryView';

const pages = ['Home', 'Darla', 'Bakery', 'My Day', 'Working for You', 'Approvals', 'Receipts', 'Memory', 'Settings'] as const;
type Page = typeof pages[number];
export type Snapshot = { tasks: Task[]; runs: Run[]; approvals: Approval[]; receipts: Receipt[]; memory: Memory[]; orders: Order[]; schedules: Schedule[]; goal: Goal | null; integrations: Record<string, string> };
const empty = (): Snapshot => ({ tasks: [], runs: [], approvals: [], receipts: [], memory: [], orders: [], schedules: [], goal: null, integrations: {} });

export default function App() {
  const [page, setPage] = useState<Page>('Home');
  const [context, setContext] = useState<Context>('bakery');
  const [connected, setConnected] = useState(false);
  const [data, setData] = useState<Snapshot>(empty);
  const [refresh, setRefresh] = useState(0);
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const api = useMemo(() => new Api(context), [context]);
  const pending = data.approvals.filter(item => item.status === 'PENDING');
  const openTasks = data.tasks.filter(task => !['COMPLETED', 'CANCELLED'].includes(task.status));
  const finished = data.runs.filter(run => run.status === 'COMPLETED' && !run.tool.startsWith('bakeoff.'));

  useEffect(() => {
    if (!connected) return;
    let cancelled = false; setLoading(true);
    void (async () => {
      try {
        const [tasks, runs, approvals, receipts, memory, schedules, integrations, goal, orders] = await Promise.all([
          api.list<Task>('/tasks'), api.list<Run>('/runs'), api.list<Approval>('/approvals'), api.list<Receipt>('/receipts'), api.list<Memory>('/memory'), api.list<Schedule>('/schedules'),
          api.request<Record<string, string>>('/integrations'), context === 'bakery' ? api.request<Goal>('/bakery/goals/weekly') : Promise.resolve(null),
          context === 'bakery' ? api.list<Order>('/orders') : Promise.resolve({ items: [] as Order[] }),
        ]);
        if (!cancelled) { setData({ tasks: tasks.items, runs: runs.items, approvals: approvals.items, receipts: receipts.items, memory: memory.items, schedules: schedules.items, integrations, goal, orders: orders.items }); setError(''); }
      } catch (caught) { if (!cancelled) { setData(empty()); setError(caught instanceof Error ? caught.message : 'Unable to load this workspace.'); } }
      finally { if (!cancelled) setLoading(false); }
    })();
    return () => { cancelled = true; };
  }, [api, connected, context, refresh]);

  const act: Action = async (work, message) => {
    setBusy(true); setError(''); setNotice('');
    try { await work(); setNotice(message); setRefresh(value => value + 1); }
    catch (caught) { setError(caught instanceof Error ? caught.message : 'This action could not be completed.'); }
    finally { setBusy(false); }
  };
  function navigate(value: Page) { setPage(value); setNotice(''); setError(''); }
  function taskForm() { return <form className="inline-form" onSubmit={event => submit(event, act, values => api.createTask({ title: String(values.get('title')) }), 'Your task is saved.')}><Field name="A task to remember"><input name="title" required maxLength={200} placeholder="What would make tomorrow easier?"/></Field><button disabled={busy} type="submit">Add task â†—</button></form>; }
  function taskList() { return data.tasks.length ? <ul className="rows">{data.tasks.map(task => <li key={task.id}><div><strong>{task.title}</strong><small>{task.due_at ? `Due ${task.due_at}` : 'No deadline set'}</small></div><Status>{task.status.toLowerCase().replaceAll('_', ' ')}</Status>{task.status === 'TODO' && <button className="quiet" disabled={busy} onClick={() => void act(() => api.request(`/tasks/${task.id}`, 'PATCH', { status: 'IN_PROGRESS' }), 'Task started.')}>Start</button>}{task.status === 'IN_PROGRESS' && <button className="quiet" disabled={busy} onClick={() => void act(() => api.request(`/tasks/${task.id}`, 'PATCH', { status: 'COMPLETED' }), 'Task marked complete by you.')}>Mark done</button>}</li>)}</ul> : <Empty>A clear page. Add something you want to keep track of.</Empty>; }

  return <div className="app"><a href="#main" className="skip">Skip to content</a>
    <aside className="sidebar"><a className="wordmark" href="#home" onClick={() => navigate('Home')}>darla<span>giggles.</span></a><p className="brand-caption">A little help. A little headspace.</p>
      <nav aria-label="Main navigation">{pages.map((item, index) => <button key={item} aria-current={page === item ? 'page' : undefined} onClick={() => navigate(item)}><span aria-hidden="true" className="nav-number">{String(index + 1).padStart(2, '0')}</span>{item}{item === 'Approvals' && pending.length > 0 && <span className="count">{pending.length}</span>}</button>)}</nav>
      <div className="sidebar-footer"><span aria-hidden="true">âœ³</span><p>Crumb &amp; Get It Bakery<small>Fresh Joy, One Crumb at a Time.</small></p></div>
    </aside><div className="workspace"><header className="topbar"><span>Room for a sweeter day.</span><label>Workspace <select aria-label="Workspace context" disabled={busy} value={context} onChange={event => { setData(empty()); setContext(event.target.value as Context); setNotice(''); setError(''); }}><option value="bakery">Bakery Â· synthetic</option><option value="personal">Personal Â· synthetic</option></select></label></header>
      <main id="main" tabIndex={-1}><div className="page-heading"><div><p className="section-label">{context === 'bakery' ? 'Crumb & Get It Bakery' : 'Your personal space'}</p><h1>{page === 'Home' ? 'A little room for what matters.' : page}</h1></div>{connected && <button className="quiet" disabled={loading || busy} onClick={() => setRefresh(value => value + 1)}>{loading ? 'Refreshingâ€¦' : 'Refresh'}</button>}</div>
        <div className="dev-note"><span className="dot" aria-hidden="true"/><p>Synthetic workspace. Runtime replies are shown when received. {data.integrations.bakeoff === 'MCP_PINNED_STAFF' ? 'task hub changes require your approval.' : 'External effects stay disabled.'}</p></div>
        {error && <div role="alert" className="message error">{error}</div>}{notice && <div role="status" className="message success">{notice}</div>}
        {!connected ? <section className="welcome panel"><span aria-hidden="true" className="welcome-flower">âœ³</span><h2>A place for the next small step.</h2><p>Try the local workspace with synthetic data. Tasks, drafts and receipts you create here are saved to the local backend.</p><button onClick={() => setConnected(true)}>Open synthetic workspace â†—</button><p className="fine-print">This development session does not sign you in as Dani.</p></section> : <>
          {loading && <p role="status" className="loading">Gathering your saved workâ€¦</p>}
          {page === 'Home' && <><section className="home-overview"><div className="intro"><h2>One thing at a time.<br/><em>You have this.</em></h2><p>Your saved priorities and decisions, with a clear view of what has actually happened.</p><button className="text-button" onClick={() => navigate('My Day')}>Make a little plan â†’</button></div><div className="weekly-summary"><span className="section-label">This week's bakery goal</span><p className="goal-value">{context === 'bakery' ? money(data.goal?.target_cents ?? 50000) : 'Your own pace'}</p><p>{context === 'bakery' ? 'Actual sales are unknown until a verified sales source is connected.' : 'Personal preferences and tasks stay in this space.'}</p><div className="goal-rule"/><small>{context === 'bakery' ? 'Drafts are plans. They do not count as completed sales.' : 'Switch to Bakery to see business records.'}</small></div></section>
            <div className="stat-strip"><div><strong>{openTasks.length}</strong><span>tasks to come back to</span></div><div><strong>{pending.length}</strong><span>decisions waiting</span></div><div><strong>{finished.length}</strong><span>verified internal runs completed</span></div></div><section className="panel"><div className="section-heading"><h2>Keep a little space for tomorrow.</h2><button className="text-button" onClick={() => navigate('My Day')}>See My Day â†’</button></div>{taskForm()}{taskList()}</section><section className="honesty-note"><span aria-hidden="true">âœ³</span><div><h3>See what Darla has received.</h3><p>Send a message in Darla. Its status shows whether the runtime has claimed it and replied. Local runs execute only on explicit worker ticks.</p></div></section></>}
          {page === 'My Day' && <section className="panel"><h2>A manageable next step.</h2><p>Keep track of the commitments in your {context} space.</p>{taskForm()}{taskList()}</section>}
          {page === 'Bakery' && (context === 'bakery' ? <BakeryView api={api} orders={data.orders} busy={busy} act={act}/> : <section className="panel"><h2>A separate space for the bakery.</h2><p>Choose the Bakery workspace to view business records.</p></section>)}
          {!['Home', 'My Day', 'Bakery'].includes(page) && <WorkViews page={page} api={api} data={data} busy={busy} act={act} close={() => { setConnected(false); setData(empty()); }}/>} </>}
        <footer className="page-footer">Made for the work. And the life around it.<span>Crumb &amp; Get It Bakery</span></footer>
      </main></div></div>;
}