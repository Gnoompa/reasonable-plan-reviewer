import { useCallback, useEffect, useMemo, useRef, useState } from 'react';

type Task = {
  id: string;
  name: string;
  owner: string | null;
  duration_days: number;
  depends_on: string[];
  quote?: string;
};

type Finding = {
  id?: string;
  title: string;
  detail: string;
  quote: string | null;
  severity?: string;
  kind?: string;
};

type ScheduleEntry = { id: string; start_day: number; end_day: number };

type TimelineEntry = {
  id: string;
  name: string;
  owner: string | null;
  start_day: number;
  end_day: number;
};

type Schedule = {
  status: 'scheduled' | 'cycle';
  schedule: ScheduleEntry[];
  end_day: number | null;
  deadline_day: number | null;
  meets_deadline: boolean | null;
  days_late: number | null;
  cycle: string[];
  optimal?: boolean | null;
  timeline?: TimelineEntry[];
};

type Review = { deadline_day: number | null; tasks: Task[]; findings: Finding[] };
type PlanSummary = { id: string; title: string };
type Plan = { id: string; text: string; review: Review; schedule: Schedule };
type Draft = { tasks: Task[]; deadline_day: number | null; dismissed: string[] };

const SEVERITY_LABEL: Record<string, string> = {
  blocks_date: 'Blocks the date',
  at_risk: 'At risk',
  note: 'Note',
};
const SEVERITY_RANK: Record<string, number> = { blocks_date: 0, at_risk: 1, note: 2 };
// Kinds the server recomputes. Kept locally so an edit can be re-judged before the server answers.
const STRUCTURAL = new Set(['cycle', 'deadline', 'late', 'owner', 'bottleneck']);

// A stable handle for one finding. Falls back to kind+title for older payloads.
function findingId(finding: Finding): string {
  return finding.id ?? `${finding.kind ?? 'model'}:${finding.title.trim().toLowerCase()}`;
}

function rank(finding: Finding): number {
  return SEVERITY_RANK[finding.severity ?? 'note'] ?? 2;
}

function fieldErrors(body: unknown): string {
  if (typeof body !== 'object' || body === null) return 'The server refused this edit.';
  const detail = (body as { detail?: unknown }).detail;
  if (typeof detail === 'string') return detail;
  if (Array.isArray(detail)) {
    return detail
      .map((item) => {
        const entry = item as { loc?: unknown[]; msg?: string };
        const where = Array.isArray(entry.loc) ? entry.loc.slice(1).join('.') : '';
        return where ? `${where}: ${entry.msg ?? 'invalid'}` : entry.msg ?? 'invalid';
      })
      .join(' · ');
  }
  return 'The server refused this edit.';
}

function draftFrom(plan: Plan): Draft {
  return {
    tasks: plan.review.tasks.map((task) => ({ ...task, depends_on: [...task.depends_on] })),
    deadline_day: plan.review.deadline_day,
    dismissed: [],
  };
}

function sameTasks(a: Task[], b: Task[]): boolean {
  if (a.length !== b.length) return false;
  return a.every((task, index) => {
    const other = b[index];
    return (
      task.id === other.id &&
      task.name === other.name &&
      task.owner === other.owner &&
      task.duration_days === other.duration_days &&
      task.depends_on.join(',') === other.depends_on.join(',')
    );
  });
}

export default function App() {
  const [plans, setPlans] = useState<PlanSummary[]>([]);
  const [selectedId, setSelectedId] = useState('');
  const [plan, setPlan] = useState<Plan | null>(null);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [live, setLive] = useState<{ schedule: Schedule; findings: Finding[] } | null>(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [saved, setSaved] = useState<string>('');
  const [showAll, setShowAll] = useState(false);
  const request = useRef(0);

  useEffect(() => {
    fetch('/api/plans')
      .then((response) => response.json())
      .then((items: PlanSummary[]) => {
        setPlans(items);
        setSelectedId(items[0]?.id ?? '');
      })
      .catch(() => setError('Could not load the plans.'));
  }, []);

  useEffect(() => {
    if (!selectedId) return;
    setPlan(null);
    setDraft(null);
    setLive(null);
    setError('');
    setSaved('');
    setShowAll(false);
    fetch(`/api/plans/${selectedId}`)
      .then(async (response) => {
        if (!response.ok) throw new Error(fieldErrors(await response.json().catch(() => ({}))));
        return response.json() as Promise<Plan>;
      })
      .then((loaded) => {
        setPlan(loaded);
        setDraft(draftFrom(loaded));
      })
      .catch((problem: Error) => setError(problem.message));
  }, [selectedId]);

  const recompute = useCallback(async (next: Draft) => {
    const ticket = ++request.current;
    try {
      const response = await fetch('/api/analyze', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ tasks: next.tasks, deadline_day: next.deadline_day }),
      });
      const body = await response.json();
      if (ticket !== request.current) return;
      if (!response.ok) {
        setError(fieldErrors(body));
        return;
      }
      setError('');
      setLive(body as { schedule: Schedule; findings: Finding[] });
    } catch {
      if (ticket === request.current) setError('Could not reach the server.');
    }
  }, []);

  useEffect(() => {
    if (!draft || !plan) return;
    if (sameTasks(draft.tasks, plan.review.tasks) && draft.deadline_day === plan.review.deadline_day) {
      setLive(null);
      return;
    }
    const timer = window.setTimeout(() => void recompute(draft), 300);
    return () => window.clearTimeout(timer);
  }, [draft, plan, recompute]);

  const schedule = live?.schedule ?? plan?.schedule ?? null;
  const edited = Boolean(live);

  const findings = useMemo(() => {
    if (!plan || !draft) return [];
    const model = plan.review.findings.filter((finding) => !STRUCTURAL.has(finding.kind ?? ''));
    const structural = live?.findings ?? plan.review.findings.filter((f) => STRUCTURAL.has(f.kind ?? ''));
    return [...structural, ...model]
      .filter((finding) => !draft.dismissed.includes(findingId(finding)))
      .sort((a, b) => rank(a) - rank(b));
  }, [plan, draft, live]);

  const moved = useMemo(() => {
    const changed = new Set<string>();
    if (!plan) return changed;
    for (const entry of schedule?.schedule ?? []) {
      const before = plan.schedule.schedule.find((item) => item.id === entry.id);
      if (!before || before.start_day !== entry.start_day || before.end_day !== entry.end_day) {
        changed.add(entry.id);
      }
    }
    return changed;
  }, [plan, schedule]);

  const patch = (id: string, changes: Partial<Task>) =>
    setDraft((current) =>
      current
        ? { ...current, tasks: current.tasks.map((task) => (task.id === id ? { ...task, ...changes } : task)) }
        : current,
    );

  const remove = (id: string) =>
    setDraft((current) =>
      current
        ? {
            ...current,
            tasks: current.tasks
              .filter((task) => task.id !== id)
              .map((task) => ({ ...task, depends_on: task.depends_on.filter((dep) => dep !== id) })),
          }
        : current,
    );

  const add = () =>
    setDraft((current) => {
      if (!current) return current;
      const used = new Set(current.tasks.map((task) => task.id));
      let number = current.tasks.length + 1;
      while (used.has(`T${number}`)) number += 1;
      return {
        ...current,
        tasks: [
          ...current.tasks,
          { id: `T${number}`, name: 'New task', owner: null, duration_days: 1, depends_on: [], quote: '' },
        ],
      };
    });

  const save = async () => {
    if (!plan || !draft) return;
    setBusy(true);
    try {
      const response = await fetch(`/api/plans/${plan.id}/corrections`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          tasks: draft.tasks,
          deadline_day: draft.deadline_day,
          dismissed: draft.dismissed,
        }),
      });
      const body = await response.json();
      if (!response.ok) {
        setError(fieldErrors(body));
        return;
      }
      setPlan({ ...plan, review: { ...plan.review, ...body.review }, schedule: body.schedule });
      setDraft(draftFrom({ ...plan, review: body.review }));
      setLive(null);
      setError('');
      setSaved(`Saved as correction ${body.correction.id}, ${body.correction.saved_at}.`);
    } catch {
      setError('Could not reach the server.');
    } finally {
      setBusy(false);
    }
  };

  const reset = () => {
    if (!plan) return;
    setDraft(draftFrom(plan));
    setLive(null);
    setError('');
    setSaved('');
  };

  const visible = showAll ? findings : findings.slice(0, 3);

  return (
    <div className="page">
      <header>
        <h1>Plan review</h1>
        <label>
          <span className="label inline">Plan</span>{' '}
          <select value={selectedId} onChange={(event) => setSelectedId(event.target.value)}>
            {plans.map((item) => (
              <option key={item.id} value={item.id}>{item.title}</option>
            ))}
          </select>
        </label>
      </header>

      {error && <p className="status error">{error}</p>}

      {plan && draft && schedule && (
        <>
          <Verdict schedule={schedule} original={plan.schedule} edited={edited} />

          <section className="card">
            <div className="row between">
              <h2 className="label">What to look at</h2>
              {edited && <span className="status inline">Recomputed from your edits</span>}
            </div>
            {findings.length === 0 && <p className="status">Nothing to flag.</p>}
            {visible.map((finding) => (
              <article className="finding" key={findingId(finding)}>
                <h3>{finding.title}</h3>
                <p>{finding.detail}</p>
                {finding.quote && <blockquote>{finding.quote}</blockquote>}
                <div className="row">
                  <span className={`chip ${finding.severity ?? 'note'}`}>
                    {SEVERITY_LABEL[finding.severity ?? 'note']}
                  </span>
                  <button
                    className="link"
                    onClick={() =>
                      setDraft((current) =>
                        current
                          ? { ...current, dismissed: [...current.dismissed, findingId(finding)] }
                          : current,
                      )
                    }
                  >
                    Dismiss
                  </button>
                </div>
              </article>
            ))}
            {findings.length > 3 && (
              <p className="more">
                <button className="link" onClick={() => setShowAll((value) => !value)}>
                  {showAll ? 'Show fewer' : `Show ${findings.length - 3} more`}
                </button>
              </p>
            )}
          </section>

          <section className="card">
            <div className="row between">
              <h2 className="label">Who is doing what</h2>
              {schedule.status === 'scheduled' && (
                <span className="status inline">
                  {schedule.optimal ? 'Shortest possible' : 'Best found'} · ends day {schedule.end_day}
                </span>
              )}
            </div>
            <Gantt schedule={schedule} moved={moved} />
          </section>

          <section className="card">
            <div className="row between">
              <h2 className="label">Tasks</h2>
              <div className="row">
                <button className="button" onClick={add}>Add task</button>
                <button className="button" onClick={reset} disabled={!edited && draft.dismissed.length === 0}>
                  Reset
                </button>
                <button className="button primary" onClick={save} disabled={busy || !edited}>
                  {busy ? 'Saving…' : 'Save correction'}
                </button>
              </div>
            </div>
            {saved && <p className="status">{saved}</p>}
            <TaskTable
              draft={draft}
              schedule={schedule}
              moved={moved}
              onPatch={patch}
              onRemove={remove}
              onDeadline={(value) => setDraft({ ...draft, deadline_day: value })}
            />
          </section>

          <section className="card">
            <details>
              <summary>The plan</summary>
              <pre>{plan.text}</pre>
            </details>
          </section>
        </>
      )}
    </div>
  );
}

function Verdict({ schedule, original, edited }: { schedule: Schedule; original: Schedule; edited: boolean }) {
  const was = original.end_day;

  if (schedule.status === 'cycle') {
    return (
      <section className="card verdict cycle">
        <h2>These tasks wait on each other</h2>
        <p>Until one of those waits is removed, the plan cannot be scheduled.</p>
      </section>
    );
  }

  const changed = edited && was !== null && schedule.end_day !== was;

  if (schedule.deadline_day === null) {
    return (
      <section className="card verdict unknown">
        <h2>No clear deadline</h2>
        <p>
          Earliest finish is day <span className="figure">{schedule.end_day ?? 0}</span>. Set a
          deadline below to check it.
          {changed && <> Was day <span className="figure">{was}</span>.</>}
        </p>
      </section>
    );
  }

  const late = schedule.meets_deadline === false;
  return (
    <section className={`card verdict${late ? ' late' : ''}`}>
      <h2>
        {late ? (
          <><span className="figure">{schedule.days_late}</span> {schedule.days_late === 1 ? 'day' : 'days'} too late</>
        ) : (
          'Makes the date'
        )}
      </h2>
      <p>
        Finishes day <span className="figure">{schedule.end_day}</span>, due day{' '}
        <span className="figure">{schedule.deadline_day}</span>.
        {changed && <> Was day <span className="figure">{was}</span>.</>}
      </p>
    </section>
  );
}

function Gantt({ schedule, moved }: { schedule: Schedule; moved: Set<string> }) {
  const timeline = schedule.timeline ?? [];
  if (schedule.status === 'cycle' || timeline.length === 0) {
    return <p className="status">No schedule to draw.</p>;
  }

  const total = Math.max(...timeline.map((entry) => entry.end_day), 1);
  const rows = new Map<string, TimelineEntry[]>();
  for (const entry of timeline) {
    const key = entry.owner ?? 'Nobody';
    rows.set(key, [...(rows.get(key) ?? []), entry]);
  }

  return (
    <div className="gantt">
      <div className="gantt-axis">
        <span />
        <div className="ticks"><span>Day 0</span><span>Day {total}</span></div>
      </div>
      {[...rows.entries()].map(([owner, entries]) => (
        <div className="gantt-row" key={owner}>
          <span className="who" title={owner}>{owner}</span>
          <div className="track">
            {entries.map((entry) => {
              const width = ((entry.end_day - entry.start_day) / total) * 100;
              return (
                <div
                  key={entry.id}
                  className={`bar${moved.has(entry.id) ? ' moved' : ''}`}
                  style={{
                    left: `${(entry.start_day / total) * 100}%`,
                    width: `${width}%`,
                  }}
                  title={`${entry.name}: day ${entry.start_day} to ${entry.end_day}`}
                >
                  {width > 9 && <span>{entry.name}</span>}
                </div>
              );
            })}
          </div>
        </div>
      ))}
    </div>
  );
}

function TaskTable({
  draft,
  schedule,
  moved,
  onPatch,
  onRemove,
  onDeadline,
}: {
  draft: Draft;
  schedule: Schedule;
  moved: Set<string>;
  onPatch: (id: string, changes: Partial<Task>) => void;
  onRemove: (id: string) => void;
  onDeadline: (value: number | null) => void;
}) {
  const byId = new Map(schedule.schedule.map((entry) => [entry.id, entry]));
  return (
    <>
      <div className="row between deadline">
        <label>
          <span className="label inline">Deadline</span>{' '}
          <input
            type="number"
            min={0}
            value={draft.deadline_day ?? ''}
            onChange={(event) => onDeadline(event.target.value === '' ? null : Number(event.target.value))}
          />
        </label>
      </div>
      <div className="table-wrap">
      <table>
        <thead>
          <tr>
            <th>Task</th>
            <th>Owner</th>
            <th className="num">Days</th>
            <th>Waits for</th>
            <th className="num">Start</th>
            <th className="num">End</th>
            <th />
          </tr>
        </thead>
        <tbody>
          {draft.tasks.map((task) => {
            const entry = byId.get(task.id);
            return (
              <tr key={task.id}>
                <td>
                  <textarea
                    className="name"
                    rows={Math.min(3, Math.max(1, Math.ceil(task.name.length / 44)))}
                    value={task.name}
                    onChange={(event) => onPatch(task.id, { name: event.target.value })}
                  />
                  {task.quote && task.quote.trim() !== task.name.trim() && (
                    <div className="quote">{task.quote}</div>
                  )}
                </td>
                <td>
                  <input
                    value={task.owner ?? ''}
                    placeholder="Nobody"
                    onChange={(event) => onPatch(task.id, { owner: event.target.value.trim() || null })}
                  />
                </td>
                <td className="num">
                  <input
                    type="number"
                    min={1}
                    value={task.duration_days}
                    onChange={(event) => onPatch(task.id, { duration_days: Number(event.target.value) })}
                  />
                </td>
                <td>
                  <input
                    value={task.depends_on.join(', ')}
                    placeholder="Nothing"
                    onChange={(event) =>
                      onPatch(task.id, {
                        depends_on: event.target.value.split(',').map((v) => v.trim()).filter(Boolean),
                      })
                    }
                  />
                </td>
                <td className="num">{entry ? entry.start_day : '—'}</td>
                <td className="num">
                  {entry ? entry.end_day : '—'}
                  {moved.has(task.id) && <span className="chip at_risk moved-chip">Moved</span>}
                </td>
                <td className="num">
                  <button className="remove" onClick={() => onRemove(task.id)} title="Remove this task">
                    ×
                  </button>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
      </div>
    </>
  );
}
