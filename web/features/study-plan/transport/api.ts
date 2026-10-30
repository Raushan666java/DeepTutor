import { apiFetch } from '@/shared/api/client'

/**
 * The study-plan wire contract (`/api/study-plans`).
 *
 * A plan is a goal plus dated sessions. Materials are references to records
 * other features own — a book id, a mastery path id, and so on — so the title
 * always comes back from the server, never from this client.
 */

export type SessionStatus = 'pending' | 'done' | 'skipped'

export type MaterialKind =
  | 'knowledge'
  | 'book'
  | 'reading'
  | 'mastery'
  | 'practice'
  | 'notebook'
  | 'document'
  | 'custom'

export interface MaterialRow {
  kind: MaterialKind
  id: string
  title: string
  detail: string
  href: string
}

export interface SessionRow {
  id: string
  date: string
  week: number
  title: string
  duration_minutes: number
  status: SessionStatus
  completed_at: string
}

export interface ProgressRow {
  total: number
  done: number
  skipped: number
  pending: number
  percent_done: number
}

export interface StudyPlan {
  id: string
  goal: string
  start_date: string
  deadline: string
  weekly_hours: number
  session_minutes: number
  weekdays: number[]
  materials: MaterialRow[]
  created_at: string
  updated_at: string
  archived: boolean
  sessions: SessionRow[]
  progress: ProgressRow
}

export interface PlanOverview {
  version: number
  updated_at: string
  today: string
  plans: StudyPlan[]
  injected_context: string
}

export interface MaterialKindRow {
  kind: MaterialKind
  label: string
}

export interface MaterialUnavailableRow {
  kind: MaterialKind
  reason: string
}

export interface MaterialList {
  materials: MaterialRow[]
  unavailable: MaterialUnavailableRow[]
  kinds: MaterialKindRow[]
}

export interface MaterialRef {
  kind: MaterialKind
  id?: string
  title?: string
}

export interface CreatePlanInput {
  goal: string
  deadline: string
  weekly_hours: number
  start_date?: string | null
  session_minutes?: number
  weekdays?: number[]
  materials?: MaterialRef[]
}

const endpoint = '/api/study-plans'

export class StudyPlanRequestError extends Error {
  constructor(
    readonly status: number,
    readonly detail: string
  ) {
    super(detail || `Study plan request failed (${status})`)
  }
}

async function json<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await apiFetch(`${endpoint}${path}`, {
    cache: 'no-store',
    ...init,
    headers: init?.body
      ? { 'Content-Type': 'application/json', ...init.headers }
      : init?.headers,
  })
  if (!response.ok) {
    let detail = ''
    try {
      detail = String((await response.json())?.detail ?? '')
    } catch {
      /* no body */
    }
    throw new StudyPlanRequestError(response.status, detail)
  }
  return response.json() as Promise<T>
}

function query(params: Record<string, string | null | undefined>): string {
  const search = new URLSearchParams()
  for (const [key, value] of Object.entries(params)) if (value != null) search.set(key, value)
  const text = search.toString()
  return text ? `?${text}` : ''
}

export function getOverview(signal?: AbortSignal) {
  return json<PlanOverview>('', { signal })
}

export function listMaterials(
  params: { query?: string; kinds?: string } = {},
  signal?: AbortSignal
) {
  return json<MaterialList>(`/materials${query(params)}`, { signal })
}

export function createPlan(input: CreatePlanInput) {
  return json<StudyPlan>('', { method: 'POST', body: JSON.stringify(input) })
}

export function setSessionStatus(planId: string, sessionId: string, status: SessionStatus) {
  return json<StudyPlan>(
    `/${encodeURIComponent(planId)}/sessions/${encodeURIComponent(sessionId)}/status`,
    { method: 'POST', body: JSON.stringify({ status }) }
  )
}

export function setPlanArchived(planId: string, archived: boolean) {
  return json<StudyPlan>(`/${encodeURIComponent(planId)}`, {
    method: 'PATCH',
    body: JSON.stringify({ archived }),
  })
}
