import type { components } from './schema';
export type Task = components['schemas']['TaskRecord'];
export type Run = components['schemas']['RunRecord'];
export type Receipt = components['schemas']['ReceiptRecord'];
export type Approval = components['schemas']['ApprovalRecord'];
export type Memory = components['schemas']['MemoryRecord'];
export type Goal = components['schemas']['WeeklyGoal'];
export type TaskCreate = components['schemas']['TaskCreate'];
export type PlanCreate = components['schemas']['BakeryPlanCreate'];
export type DraftCreate = components['schemas']['OrderDraftCreate'];
export type BatchCreate = components['schemas']['BatchCreate'];
export type ScheduleCreate = components['schemas']['ScheduleCreate'];
export type RunCreate = components['schemas']['RunCreate'];
export type Plan = components['schemas']['PlanRecord'];
export type Order = components['schemas']['OrderRecord'];
export type Batch = components['schemas']['BatchRecord'];
export type Schedule = components['schemas']['ScheduleRecord'];
export type Context = 'bakery' | 'personal';

export class ApiError extends Error {
  constructor(public status: number, message: string, public correlationId?: string) { super(message); }
}

export class Api {
  constructor(private context: Context) {}
  async request<T>(path: string, method = 'GET', body?: unknown, key?: string): Promise<T> {
    const headers: Record<string, string> = { Authorization: `Bearer synthetic-dani-${this.context}` };
    if (body !== undefined) headers['Content-Type'] = 'application/json';
    if (method !== 'GET') headers['Idempotency-Key'] = key ?? crypto.randomUUID();
    const response = await fetch(`/api/v1${path}`, { method, headers, body: body === undefined ? undefined : JSON.stringify(body), cache: 'no-store' });
    const result = await response.json();
    if (!response.ok) throw new ApiError(response.status, result.error?.message ?? 'Darla could not complete this request.', result.error?.correlation_id);
    return result as T;
  }
  list<T>(path: string) { return this.request<{ items: T[]; total: number; limit: number; offset: number }>(path); }
  createTask(body: TaskCreate) { return this.request<Task>('/tasks', 'POST', body); }
  createPlan(body: PlanCreate) { return this.request<Plan>('/bakery/plans', 'POST', body); }
  createDraft(body: DraftCreate) { return this.request<Order>('/orders/drafts', 'POST', body); }
  createBatch(body: BatchCreate) { return this.request<Batch>('/bakery/batch-plans', 'POST', body); }
  createSchedule(body: ScheduleCreate) { return this.request<Schedule>('/schedules', 'POST', body); }
  queueRun(body: RunCreate) { return this.request<Run>('/runs', 'POST', body); }
}