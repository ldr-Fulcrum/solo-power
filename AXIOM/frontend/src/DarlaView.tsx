import { useEffect, useState } from 'react';
import { Api } from './api/client';
import { Field, Status, submit, type Action } from './ui';

type Message = { id: string; text: string; status: string; thread_id: string };
type Entry = { id: string; text: string; role: string; provenance_references?: string[] };
type Props = { api: Api; busy: boolean; act: Action };

export function DarlaView({ api, busy, act }: Props) {
  const [messages, setMessages] = useState<Message[]>([]);
  const [thread, setThread] = useState('');
  const [entries, setEntries] = useState<Entry[]>([]);
  const [error, setError] = useState('');
  useEffect(() => { setThread(''); setEntries([]); setMessages([]); }, [api]);
  useEffect(() => {
    let active = true;
    let pending = false;
    async function refresh() {
      if (pending) return;
      pending = true;
      try {
        const result = await api.list<Message>('/assistant/messages');
        const history = thread ? await api.request<{ messages: Entry[] }>(`/assistant/threads/${thread}`) : null;
        if (active) { setMessages(result.items); setEntries(history?.messages ?? []); setError(''); }
      } catch (caught) { if (active) setError(caught instanceof Error ? caught.message : 'Unable to load messages.'); }
      finally { pending = false; }
    }
    void refresh();
    const timer = window.setInterval(() => void refresh(), 3000);
    return () => { active = false; window.clearInterval(timer); };
  }, [api, thread]);
  return <section className="panel conversation"><h2>Tell me what needs doing.</h2>
    <p>Your message is saved for the runtime. A queued message is waiting for an answer. Replies and model references are reported by the runtime.</p>
    {error && <p role="alert">{error}</p>}
    <form onSubmit={event => submit(event, act, async values => {
      const message = await api.request<Message>('/assistant/messages', 'POST', { message: String(values.get('message')), thread_id: thread || null });
      setThread(message.thread_id);
    }, 'Message saved for the runtime.')}><Field name="Your message"><textarea name="message" required maxLength={4000} placeholder="Help me plan tomorrow’s bake."/></Field><button type="submit" disabled={busy}>Send to Darla</button></form>
    <ol>{messages.map(message => <li key={message.id}><button className="text-button" onClick={() => setThread(message.thread_id)}>{message.text}</button> <Status>{message.status}</Status>{['QUEUED', 'CLAIMED'].includes(message.status) && <button disabled={busy} className="quiet" onClick={() => void act(() => api.request(`/assistant/messages/${message.id}/cancel`, 'POST', {}), 'Message cancelled. New replies are blocked.')}>Cancel message</button>}</li>)}</ol>
    <div aria-live="polite">{entries.map(entry => <article key={entry.id}><h3>{entry.role === 'user' ? 'You' : 'Darla'}</h3><p style={{ whiteSpace: 'pre-wrap' }}>{entry.text}</p>{entry.provenance_references?.length ? <details><summary>Runtime-reported provenance</summary><ul>{entry.provenance_references.map((reference, index) => <li key={index}>{reference}</li>)}</ul></details> : null}</article>)}</div>
  </section>;
}
