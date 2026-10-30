'use client'

import Link from 'next/link'
import { useEffect, useMemo, useState, type FormEvent } from 'react'
import { Archive, Check, Loader2, RotateCcw, Search, SkipForward } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { FeaturePage, PageHeader, pageActionClass } from '@/components/layout/FeaturePage'
import { useActiveWorkspaceId } from '@/hooks/useActiveWorkspaceId'
import { scopedUrl } from '@/lib/workspace-scope'
import * as api from '../transport/api'

const CARD = 'rounded-xl border border-[var(--border)] bg-[var(--card)] p-5 space-y-4'
const FIELD =
  'w-full rounded-lg border border-[var(--border)] bg-[var(--background)] px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-[var(--ring)]'
const GHOST =
  'inline-flex items-center gap-1.5 rounded-lg border border-[var(--border)] px-2.5 py-1.5 text-xs font-medium hover:bg-[var(--muted)] disabled:opacity-50'
const WEEKDAYS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']

function messageOf(error: unknown): string {
  if (error instanceof api.StudyPlanRequestError) return error.detail || error.message
  return error instanceof Error ? error.message : 'The request failed.'
}

function byWeek(sessions: api.SessionRow[]): { week: number; sessions: api.SessionRow[] }[] {
  const groups = new Map<number, api.SessionRow[]>()
  for (const session of sessions) {
    const bucket = groups.get(session.week)
    if (bucket) bucket.push(session)
    else groups.set(session.week, [session])
  }
  return [...groups.entries()].sort(([a], [b]) => a - b).map(([week, rows]) => ({ week, sessions: rows }))
}

export default function StudyPlanPage() {
  const { t } = useTranslation()
  const workspace = useActiveWorkspaceId()
  const [revision, setRevision] = useState(0)
  const [overview, setOverview] = useState<api.PlanOverview | null>(null)
  const [materials, setMaterials] = useState<api.MaterialList | null>(null)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [pending, setPending] = useState<string | null>(null)
  const [showArchived, setShowArchived] = useState(false)

  const [goal, setGoal] = useState('')
  const [deadline, setDeadline] = useState('')
  const [startDate, setStartDate] = useState('')
  const [weeklyHours, setWeeklyHours] = useState('6')
  const [sessionMinutes, setSessionMinutes] = useState('60')
  const [days, setDays] = useState<number[]>([0, 2, 4])
  const [picked, setPicked] = useState<string[]>([])
  const [customTopics, setCustomTopics] = useState<string[]>([])
  const [customTopic, setCustomTopic] = useState('')
  const [query, setQuery] = useState('')

  useEffect(() => {
    const controller = new AbortController()
    void Promise.all([api.getOverview(controller.signal), api.listMaterials({}, controller.signal)])
      .then(([nextOverview, nextMaterials]) => {
        if (controller.signal.aborted) return
        setOverview(nextOverview)
        setMaterials(nextMaterials)
        setError('')
      })
      .catch((cause) => {
        if (!controller.signal.aborted) setError(messageOf(cause))
      })
    return () => controller.abort()
  }, [workspace, revision])

  const groups = useMemo(() => {
    if (!materials) return []
    const needle = query.trim().toLowerCase()
    return materials.kinds
      .map((kind) => ({
        kind: kind.kind,
        label: kind.label,
        rows: materials.materials.filter(
          (row) =>
            row.kind === kind.kind &&
            (!needle ||
              row.title.toLowerCase().includes(needle) ||
              row.detail.toLowerCase().includes(needle))
        ),
      }))
      .filter((group) => group.rows.length > 0)
  }, [materials, query])

  const active = overview?.plans.filter((plan) => !plan.archived) ?? []
  const archived = overview?.plans.filter((plan) => plan.archived) ?? []

  function toggleDay(day: number) {
    setDays((current) =>
      current.includes(day) ? current.filter((item) => item !== day) : [...current, day].sort()
    )
  }

  function togglePicked(key: string) {
    setPicked((current) =>
      current.includes(key) ? current.filter((item) => item !== key) : [...current, key]
    )
  }

  async function submit(event: FormEvent) {
    event.preventDefault()
    setPending('create')
    setError('')
    setNotice('')
    const refs: api.MaterialRef[] = picked.map((key) => {
      const split = key.indexOf(':')
      return { kind: key.slice(0, split) as api.MaterialKind, id: key.slice(split + 1) }
    })
    for (const title of customTopics) refs.push({ kind: 'custom', title })
    try {
      await api.createPlan({
        goal: goal.trim(),
        deadline,
        weekly_hours: Number(weeklyHours),
        start_date: startDate || null,
        session_minutes: Number(sessionMinutes),
        weekdays: days,
        materials: refs,
      })
      setGoal('')
      setDeadline('')
      setStartDate('')
      setPicked([])
      setCustomTopics([])
      setNotice(t('Plan created.'))
      setRevision((value) => value + 1)
    } catch (cause) {
      setError(messageOf(cause))
    } finally {
      setPending(null)
    }
  }

  async function mark(plan: api.StudyPlan, session: api.SessionRow, status: api.SessionStatus) {
    setPending(`${plan.id}:${session.id}`)
    setError('')
    try {
      const updated = await api.setSessionStatus(plan.id, session.id, status)
      setOverview((current) =>
        current
          ? { ...current, plans: current.plans.map((row) => (row.id === updated.id ? updated : row)) }
          : current
      )
    } catch (cause) {
      setError(messageOf(cause))
    } finally {
      setPending(null)
    }
  }

  async function archive(plan: api.StudyPlan, next: boolean) {
    setPending(plan.id)
    setError('')
    try {
      const updated = await api.setPlanArchived(plan.id, next)
      setOverview((current) =>
        current
          ? { ...current, plans: current.plans.map((row) => (row.id === updated.id ? updated : row)) }
          : current
      )
    } catch (cause) {
      setError(messageOf(cause))
    } finally {
      setPending(null)
    }
  }

  return (
    <FeaturePage>
      <PageHeader
        title={t('Study plan')}
        description={t(
          'A goal and a deadline become dated sessions you can tick off. Scheduling is deterministic: you pick the materials, the planner picks the dates.'
        )}
        action={
          <button
            type="button"
            className={pageActionClass('secondary')}
            onClick={() => setRevision((value) => value + 1)}
          >
            {t('Refresh')}
          </button>
        }
      />

      {error ? (
        <div role="alert" className={`${CARD} border-[var(--destructive)] text-sm`}>
          {error}
        </div>
      ) : null}
      {notice ? (
        <div role="status" className={`${CARD} text-sm`}>
          {notice}
        </div>
      ) : null}

      <section className="mb-8" aria-labelledby="study-plan-new">
        <h2 id="study-plan-new" className="mb-3 text-[15px] font-semibold">
          {t('New plan')}
        </h2>
        <form className={CARD} onSubmit={submit}>
          <div className="grid gap-4 md:grid-cols-2">
            <label className="block space-y-1.5 md:col-span-2">
              <span className="text-xs font-medium text-[var(--muted-foreground)]">
                {t('What do you want to be able to do?')}
              </span>
              <input
                className={FIELD}
                value={goal}
                onChange={(event) => setGoal(event.target.value)}
                maxLength={200}
                placeholder={t('Ship a retrieval-augmented search service')}
                required
              />
            </label>
            <label className="block space-y-1.5">
              <span className="text-xs font-medium text-[var(--muted-foreground)]">
                {t('Deadline')}
              </span>
              <input
                type="date"
                className={FIELD}
                value={deadline}
                onChange={(event) => setDeadline(event.target.value)}
                required
              />
            </label>
            <label className="block space-y-1.5">
              <span className="text-xs font-medium text-[var(--muted-foreground)]">
                {t('Start (optional)')}
              </span>
              <input
                type="date"
                className={FIELD}
                value={startDate}
                onChange={(event) => setStartDate(event.target.value)}
              />
            </label>
            <label className="block space-y-1.5">
              <span className="text-xs font-medium text-[var(--muted-foreground)]">
                {t('Hours per week')}
              </span>
              <input
                type="number"
                min={0.5}
                max={60}
                step={0.5}
                className={FIELD}
                value={weeklyHours}
                onChange={(event) => setWeeklyHours(event.target.value)}
                required
              />
            </label>
            <label className="block space-y-1.5">
              <span className="text-xs font-medium text-[var(--muted-foreground)]">
                {t('Minutes per session')}
              </span>
              <input
                type="number"
                min={5}
                max={480}
                step={5}
                className={FIELD}
                value={sessionMinutes}
                onChange={(event) => setSessionMinutes(event.target.value)}
              />
            </label>
          </div>

          <fieldset>
            <legend className="mb-2 text-xs font-medium text-[var(--muted-foreground)]">
              {t('Study days')}
            </legend>
            <div className="flex flex-wrap gap-2">
              {WEEKDAYS.map((label, index) => (
                <button
                  key={label}
                  type="button"
                  aria-pressed={days.includes(index)}
                  onClick={() => toggleDay(index)}
                  className={`rounded-lg border px-3 py-1.5 text-xs font-medium ${
                    days.includes(index)
                      ? 'border-[var(--primary)] bg-[var(--primary)] text-[var(--primary-foreground)]'
                      : 'border-[var(--border)] hover:bg-[var(--muted)]'
                  }`}
                >
                  {t(label)}
                </button>
              ))}
            </div>
          </fieldset>

          <div>
            <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
              <h3 className="text-xs font-medium text-[var(--muted-foreground)]">{t('Materials')}</h3>
              <label className="flex items-center gap-2 text-xs text-[var(--muted-foreground)]">
                <Search size={14} aria-hidden />
                <input
                  className="rounded-lg border border-[var(--border)] bg-[var(--background)] px-2 py-1 text-xs"
                  value={query}
                  onChange={(event) => setQuery(event.target.value)}
                  placeholder={t('Filter materials')}
                  aria-label={t('Filter materials')}
                />
              </label>
            </div>
            {!materials ? (
              <p className="text-sm text-[var(--muted-foreground)]">{t('Loading…')}</p>
            ) : groups.length === 0 ? (
              <p className="text-sm text-[var(--muted-foreground)]">
                {t('No materials yet. A custom topic still works.')}
              </p>
            ) : (
              <div className="space-y-3">
                {groups.map((group) => (
                  <div key={group.kind}>
                    <p className="mb-1.5 text-[11px] font-semibold uppercase tracking-wide text-[var(--muted-foreground)]">
                      {group.label}
                    </p>
                    <div
                      className={`grid gap-2 sm:grid-cols-2 ${
                        group.rows.length > 12 ? 'max-h-72 overflow-y-auto pr-1' : ''
                      }`}
                    >
                      {group.rows.map((row) => {
                        const key = `${row.kind}:${row.id}`
                        return (
                          <label
                            key={key}
                            className="flex cursor-pointer items-start gap-2 rounded-lg border border-[var(--border)] px-3 py-2 text-sm hover:bg-[var(--muted)]"
                          >
                            <input
                              type="checkbox"
                              className="mt-0.5"
                              checked={picked.includes(key)}
                              onChange={() => togglePicked(key)}
                            />
                            <span className="min-w-0">
                              <span className="block truncate font-medium">{row.title}</span>
                              {row.detail ? (
                                <span className="block truncate text-xs text-[var(--muted-foreground)]">
                                  {row.detail}
                                </span>
                              ) : null}
                            </span>
                          </label>
                        )
                      })}
                    </div>
                  </div>
                ))}
              </div>
            )}
            {materials?.unavailable.length ? (
              <ul className="mt-2 space-y-1 text-xs text-[var(--muted-foreground)]">
                {materials.unavailable.map((row) => (
                  <li key={row.kind}>
                    {row.kind}: {row.reason}
                  </li>
                ))}
              </ul>
            ) : null}
          </div>

          <div className="space-y-2">
            <label className="block space-y-1.5">
              <span className="text-xs font-medium text-[var(--muted-foreground)]">
                {t('Custom topic')}
              </span>
              <div className="flex gap-2">
                <input
                  className={FIELD}
                  value={customTopic}
                  onChange={(event) => setCustomTopic(event.target.value)}
                  maxLength={120}
                  placeholder={t('Write the evaluation harness')}
                />
                <button
                  type="button"
                  className={GHOST}
                  disabled={!customTopic.trim()}
                  onClick={() => {
                    const title = customTopic.trim()
                    if (!title) return
                    setCustomTopics((current) => [...current, title])
                    setCustomTopic('')
                  }}
                >
                  {t('Add')}
                </button>
              </div>
            </label>
            {customTopics.length ? (
              <ul className="flex flex-wrap gap-2">
                {customTopics.map((title) => (
                  <li
                    key={title}
                    className="inline-flex items-center gap-2 rounded-full border border-[var(--border)] px-3 py-1 text-xs"
                  >
                    {title}
                    <button
                      type="button"
                      aria-label={t('Remove')}
                      className="text-[var(--muted-foreground)] hover:text-[var(--foreground)]"
                      onClick={() =>
                        setCustomTopics((current) => current.filter((item) => item !== title))
                      }
                    >
                      ×
                    </button>
                  </li>
                ))}
              </ul>
            ) : null}
          </div>

          <div className="flex items-center gap-3">
            <button
              type="submit"
              className={pageActionClass('primary')}
              disabled={pending === 'create' || !goal.trim() || !deadline}
            >
              {pending === 'create' ? <Loader2 size={14} className="animate-spin" aria-hidden /> : null}
              {pending === 'create' ? t('Creating…') : t('Create plan')}
            </button>
            <p className="text-xs text-[var(--muted-foreground)]">
              {t('Sessions are placed on your study days until the deadline.')}
            </p>
          </div>
        </form>
      </section>

      <section aria-labelledby="study-plan-list">
        <h2 id="study-plan-list" className="mb-3 text-[15px] font-semibold">
          {t('Your plans')}
        </h2>
        {!overview ? (
          <p className="text-sm text-[var(--muted-foreground)]">{t('Loading…')}</p>
        ) : active.length === 0 ? (
          <p className="text-sm text-[var(--muted-foreground)]">
            {t('No plans yet. Create one above.')}
          </p>
        ) : (
          <div className="space-y-4">
            {active.map((plan) => (
              <PlanCard
                key={plan.id}
                plan={plan}
                today={overview.today}
                pending={pending}
                onMark={mark}
                onArchive={archive}
              />
            ))}
          </div>
        )}

        {archived.length ? (
          <div className="mt-6">
            <button
              type="button"
              className={GHOST}
              aria-expanded={showArchived}
              onClick={() => setShowArchived((value) => !value)}
            >
              <Archive size={14} aria-hidden />
              {showArchived ? t('Hide archived') : t('Show archived')} ({archived.length})
            </button>
            {showArchived ? (
              <div className="mt-3 space-y-4">
                {archived.map((plan) => (
                  <PlanCard
                    key={plan.id}
                    plan={plan}
                    today={overview?.today ?? ''}
                    pending={pending}
                    onMark={mark}
                    onArchive={archive}
                  />
                ))}
              </div>
            ) : null}
          </div>
        ) : null}
      </section>
    </FeaturePage>
  )
}

function PlanCard({
  plan,
  today,
  pending,
  onMark,
  onArchive,
}: {
  plan: api.StudyPlan
  today: string
  pending: string | null
  onMark: (plan: api.StudyPlan, session: api.SessionRow, status: api.SessionStatus) => void
  onArchive: (plan: api.StudyPlan, archived: boolean) => void
}) {
  const { t } = useTranslation()
  return (
    <article className={CARD} aria-label={plan.goal}>
      <header className="space-y-2">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="min-w-0">
            <h3 className="font-semibold break-words">{plan.goal}</h3>
            <p className="mt-1 text-xs text-[var(--muted-foreground)]">
              {plan.start_date} → {plan.deadline} ·{' '}
              {t('{{hours}} hours per week', { hours: plan.weekly_hours })} ·{' '}
              {t('{{minutes}} minute sessions', { minutes: plan.session_minutes })} ·{' '}
              {t('{{done}} of {{total}} sessions done', {
                done: plan.progress.done,
                total: plan.progress.total,
              })}
            </p>
          </div>
          <button
            type="button"
            className={GHOST}
            disabled={pending === plan.id}
            onClick={() => onArchive(plan, !plan.archived)}
          >
            <Archive size={14} aria-hidden />
            {plan.archived ? t('Restore') : t('Archive')}
          </button>
        </div>
        <div
          className="h-1.5 overflow-hidden rounded-full bg-[var(--muted)]"
          role="progressbar"
          aria-valuenow={plan.progress.percent_done}
          aria-valuemin={0}
          aria-valuemax={100}
          aria-label={t('Plan progress')}
        >
          <div
            className="h-full rounded-full bg-[var(--primary)] transition-[width]"
            style={{ width: `${plan.progress.percent_done}%` }}
          />
        </div>
      </header>

      {plan.materials.length ? (
        <ul className="flex flex-wrap gap-2">
          {plan.materials.map((material, index) => {
            const external = /^https?:\/\//i.test(material.href)
            const chip =
              'inline-flex items-center gap-1 rounded-full border border-[var(--border)] px-3 py-1 text-xs hover:bg-[var(--muted)]'
            const body = (
              <>
                <span className="text-[var(--muted-foreground)]">{material.kind}</span>
                {material.title}
              </>
            )
            return (
              <li key={`${material.kind}:${material.id}:${index}`}>
                {!material.href ? (
                  <span className={chip}>{body}</span>
                ) : external ? (
                  // A course chapter lives on the learner's published site.
                  <a className={chip} href={material.href} target="_blank" rel="noreferrer">
                    {body}
                  </a>
                ) : (
                  <Link className={chip} href={scopedUrl(material.href)}>
                    {body}
                  </Link>
                )}
              </li>
            )
          })}
        </ul>
      ) : null}

      <div className="space-y-4">
        {byWeek(plan.sessions).map((group) => (
          <div key={group.week}>
            <p className="mb-1.5 text-[11px] font-semibold uppercase tracking-wide text-[var(--muted-foreground)]">
              {t('Week {{week}}', { week: group.week })}
            </p>
            <ol className="space-y-1.5">
              {group.sessions.map((session) => {
                const busy = pending === `${plan.id}:${session.id}`
                return (
                  <li
                    key={session.id}
                    className={`flex flex-wrap items-center gap-3 rounded-lg border px-3 py-2 text-sm ${
                      session.date === today
                        ? 'border-[var(--primary)]'
                        : 'border-[var(--border)]'
                    }`}
                  >
                    <time
                      dateTime={session.date}
                      className="w-24 shrink-0 text-xs text-[var(--muted-foreground)]"
                    >
                      {session.date}
                      {session.date === today ? ` · ${t('today')}` : ''}
                    </time>
                    <span
                      className={`min-w-0 flex-1 break-words ${
                        session.status === 'skipped'
                          ? 'text-[var(--muted-foreground)] line-through'
                          : ''
                      }`}
                    >
                      {session.title}
                    </span>
                    <span className="text-xs text-[var(--muted-foreground)]">
                      {session.duration_minutes} {t('min')}
                    </span>
                    <span className="text-xs font-medium">
                      {session.status === 'done'
                        ? t('done')
                        : session.status === 'skipped'
                          ? t('skipped')
                          : t('pending')}
                    </span>
                    <span className="flex items-center gap-1">
                      <button
                        type="button"
                        className={GHOST}
                        disabled={busy || session.status === 'done'}
                        onClick={() => onMark(plan, session, 'done')}
                      >
                        <Check size={14} aria-hidden />
                        {t('Done')}
                      </button>
                      <button
                        type="button"
                        className={GHOST}
                        disabled={busy || session.status === 'skipped'}
                        onClick={() => onMark(plan, session, 'skipped')}
                      >
                        <SkipForward size={14} aria-hidden />
                        {t('Skip')}
                      </button>
                      <button
                        type="button"
                        className={GHOST}
                        disabled={busy || session.status === 'pending'}
                        onClick={() => onMark(plan, session, 'pending')}
                      >
                        <RotateCcw size={14} aria-hidden />
                        {t('Reset')}
                      </button>
                    </span>
                  </li>
                )
              })}
            </ol>
          </div>
        ))}
      </div>
    </article>
  )
}
