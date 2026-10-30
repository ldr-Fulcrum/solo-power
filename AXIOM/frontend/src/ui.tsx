import type { FormEvent, ReactNode } from 'react';
export type Action = (work: () => Promise<unknown>, message: string) => Promise<void>;
export function Field({ name, children }: { name: string; children: ReactNode }) { return <label className="field"><span>{name}</span>{children}</label>; }
export function Empty({ children }: { children: ReactNode }) { return <p className="empty">{children}</p>; }
export function Status({ children }: { children: ReactNode }) { return <span className="status">{children}</span>; }
export function submit(event: FormEvent<HTMLFormElement>, act: Action, work: (data: FormData) => Promise<unknown>, message: string) {
  event.preventDefault(); const form = event.currentTarget; const data = new FormData(form);
  void act(async () => { await work(data); form.reset(); }, message);
}
export const label = (value: string) => value.toLowerCase().replaceAll('_', ' ');
export const money = (cents: number) => new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD' }).format(cents / 100);