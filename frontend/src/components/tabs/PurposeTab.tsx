"use client";

import { useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { BookOpen, CheckCircle2, CircleAlert, CircleX, Compass, FileSearch, Lightbulb, Loader2, Rocket, Target } from "lucide-react";
import { api, post, useApi } from "@/lib/api";
import { FilePicker } from "@/components/FilePicker";
import { Badge, Bar, CodeBlock, Card, Empty, ErrorBox, FileRef, Spinner } from "@/components/ui";

const STATUS_ICON: Record<string, React.ReactNode> = {
  pass: <CheckCircle2 className="h-4 w-4 text-emerald-600" />,
  warn: <CircleAlert className="h-4 w-4 text-amber-500" />,
  fail: <CircleX className="h-4 w-4 text-red-600" />,
};

function SuggestionList({ items, empty }: { items: any[]; empty: string }) {
  if (!items?.length) return <Empty>{empty}</Empty>;
  return (
    <ul className="space-y-3">
      {items.map((s) => (
        <li key={s.title} className="rounded-lg border border-slate-200 p-3">
          <div className="flex items-start justify-between gap-2">
            <span className="font-semibold text-slate-900">{s.title}</span>
            <Badge tone={s.priority === "high" ? "high" : s.priority === "medium" ? "medium" : "low"}>{s.priority}</Badge>
          </div>
          <p className="mt-1 text-sm text-slate-600">{s.reason}</p>
        </li>
      ))}
    </ul>
  );
}

export function PurposeTab({ repoId }: { repoId: string }) {
  const { data, error, loading } = useApi<any>(`/repositories/${repoId}/purpose`);
  const [goal, setGoal] = useState("");
  const [fit, setFit] = useState<any>(null);
  const [fitBusy, setFitBusy] = useState(false);
  const [fitError, setFitError] = useState<string | null>(null);
  const [path, setPath] = useState("");
  const [explained, setExplained] = useState<any>(null);
  const [explainBusy, setExplainBusy] = useState(false);

  async function runFit() {
    setFitBusy(true);
    setFitError(null);
    try {
      setFit(await post(`/repositories/${repoId}/purpose/fit`, { goal }));
    } catch (e) {
      setFitError((e as Error).message);
    } finally {
      setFitBusy(false);
    }
  }

  async function explain(p: string) {
    setPath(p);
    setExplainBusy(true);
    try {
      setExplained(await api(`/repositories/${repoId}/explain?path=${encodeURIComponent(p)}`));
    } catch (e) {
      setExplained({ error: (e as Error).message });
    } finally {
      setExplainBusy(false);
    }
  }

  if (loading) return <Spinner />;
  if (error) return <ErrorBox message={error} />;
  if (!data) return null;
  const ready = data.enterprise_readiness;

  return (
    <div className="space-y-5">
      <Card title="What this repository is & what it solves">
        <div className="mb-3 flex flex-wrap items-center gap-2">
          <Badge tone="medium">{data.project_type.type}</Badge>
          {data.domains.map((d: any) => (
            <Badge key={d.domain} tone="low">
              {d.domain} · {Math.round(d.confidence * 100)}%
            </Badge>
          ))}
        </div>
        <p className="text-base leading-relaxed text-slate-700">{data.what_it_solves}</p>
        {data.project_type.signals.length > 0 && <p className="mt-2 text-xs text-slate-400">Signals: {data.project_type.signals.join(" · ")}</p>}
        {data.ai_brief && (
          <div className="markdown mt-4 rounded-lg border border-brand-100 bg-brand-50/50 p-4 text-sm">
            <ReactMarkdown remarkPlugins={[remarkGfm]}>{data.ai_brief}</ReactMarkdown>
          </div>
        )}
      </Card>

      <div className="grid gap-5 lg:grid-cols-2">
        <Card title="Key features">
          {data.key_features.length ? (
            <ul className="list-disc space-y-1 pl-5 text-sm">
              {data.key_features.map((f: string) => (
                <li key={f}>{f}</li>
              ))}
            </ul>
          ) : (
            <Empty>No features documented.</Empty>
          )}
        </Card>
        <Card title="How to run it">
          {data.how_to_run.length ? (
            <ul className="space-y-2">
              {data.how_to_run.map((s: any) => (
                <li key={s.command}>
                  <code className="block rounded bg-slate-900 px-2 py-1 font-mono text-xs text-emerald-300">{s.command}</code>
                  <span className="text-xs text-slate-500">{s.purpose}</span>
                </li>
              ))}
            </ul>
          ) : (
            <Empty>No build/run commands detected.</Empty>
          )}
        </Card>
      </div>

      <Card title={<span className="flex items-center gap-2"><Target className="h-4 w-4" /> Fit for our purpose</span>}>
        <p className="mb-2 text-sm text-slate-500">Describe what your team wants to build. The platform maps your goal onto this codebase and shows what to reuse, what is missing and a plan.</p>
        <textarea
          className="input h-24"
          placeholder="e.g. We need an appointment-booking platform for our clinics with SSO, notifications and reporting"
          value={goal}
          onChange={(e) => setGoal(e.target.value)}
          maxLength={3000}
        />
        <button className="btn-primary mt-2" disabled={fitBusy || goal.trim().length < 5} onClick={() => void runFit()}>
          {fitBusy ? <Loader2 className="h-4 w-4 animate-spin" /> : <Compass className="h-4 w-4" />} Analyze fit
        </button>
        {fitError && <div className="mt-3"><ErrorBox message={fitError} /></div>}
        {fit && (
          <div className="mt-5 space-y-4">
            <div className="flex flex-wrap items-center gap-4">
              <div className="text-4xl font-bold text-slate-900">{fit.fit_score}%</div>
              <div className="flex-1">
                <div className="font-semibold">{fit.verdict}</div>
                <Bar value={fit.fit_score} className={fit.fit_score >= 70 ? "bg-emerald-500" : fit.fit_score >= 35 ? "bg-amber-400" : "bg-red-500"} />
              </div>
            </div>
            <div className="flex flex-wrap gap-1 text-xs">
              {fit.covered_terms.map((t: string) => <Badge key={t} tone="low">✓ {t}</Badge>)}
              {fit.gaps.map((t: string) => <Badge key={t} tone="high">✗ {t}</Badge>)}
            </div>
            <div className="grid gap-4 md:grid-cols-2">
              <div>
                <div className="card-title mb-2">Plan</div>
                <ol className="list-decimal space-y-1 pl-5 text-sm">
                  {fit.plan.map((p: string) => <li key={p}>{p}</li>)}
                </ol>
              </div>
              <div>
                <div className="card-title mb-2">Reusable components</div>
                <ul className="space-y-1 text-sm">
                  {fit.reusable_components.map((r: any) => (
                    <li key={r.file}>
                      <FileRef path={r.file} /> <span className="text-xs text-slate-400">{r.symbols.join(", ")}</span>
                    </li>
                  ))}
                </ul>
                {fit.matching_apis.length > 0 && <div className="mt-2 text-xs text-slate-500">APIs: {fit.matching_apis.join(", ")}</div>}
                {fit.matching_entities.length > 0 && <div className="mt-1 text-xs text-slate-500">Entities: {fit.matching_entities.join(", ")}</div>}
              </div>
            </div>
            {fit.ai_advice && (
              <div className="markdown rounded-lg border border-brand-100 bg-brand-50/50 p-4 text-sm">
                <ReactMarkdown remarkPlugins={[remarkGfm]}>{fit.ai_advice}</ReactMarkdown>
              </div>
            )}
          </div>
        )}
      </Card>

      <div className="grid gap-5 lg:grid-cols-3">
        <Card title={<span className="flex items-center gap-2"><Rocket className="h-4 w-4" /> Reuse / adopt</span>}>
          <SuggestionList items={data.suggestions.reuse} empty="No reuse candidates detected." />
        </Card>
        <Card title={<span className="flex items-center gap-2"><Lightbulb className="h-4 w-4" /> Develop next</span>}>
          <SuggestionList items={data.suggestions.extend} empty="No gaps detected." />
        </Card>
        <Card title="Improve code health">
          <SuggestionList items={data.suggestions.improve} empty="Code health looks good." />
        </Card>
      </div>

      <Card title={`Enterprise readiness — ${ready.score}/100 · ${ready.level}`}>
        <div className="mb-4">
          <Bar value={ready.score} className={ready.score >= 80 ? "bg-emerald-500" : ready.score >= 55 ? "bg-amber-400" : "bg-red-500"} />
        </div>
        <table className="table">
          <thead>
            <tr>
              <th />
              <th>Area</th>
              <th>Finding</th>
              <th>Recommended action</th>
            </tr>
          </thead>
          <tbody>
            {ready.checks.map((c: any) => (
              <tr key={c.area}>
                <td>{STATUS_ICON[c.status]}</td>
                <td className="font-medium">{c.area}</td>
                <td className="text-sm text-slate-600">{c.detail}</td>
                <td className="text-xs text-slate-500">{c.status === "pass" ? "—" : c.action}</td>
              </tr>
            ))}
          </tbody>
        </table>
        <div className="mt-3 rounded bg-slate-50 p-3 text-sm">
          <strong>License: {ready.license.name}</strong> {ready.license.file && <FileRef path={ready.license.file} />} — {ready.license.guidance}
        </div>
      </Card>

      <div className="grid gap-5 lg:grid-cols-2">
        <Card title="Integration points">
          <div className="space-y-3 text-sm">
            {data.integration.api_groups.length > 0 && (
              <div>
                <div className="mb-1 text-xs font-semibold uppercase text-slate-400">API groups {data.integration.openapi ? "(OpenAPI available)" : ""}</div>
                {data.integration.api_groups.map((g: any) => (
                  <details key={g.prefix}>
                    <summary className="cursor-pointer font-mono text-xs">
                      {g.prefix} <span className="text-slate-400">({g.count})</span>
                    </summary>
                    <div className="pl-4 font-mono text-xs text-slate-500">{g.endpoints.join(" · ")}</div>
                  </details>
                ))}
              </div>
            )}
            {data.integration.events.length > 0 && (
              <div>
                <div className="mb-1 text-xs font-semibold uppercase text-slate-400">Events</div>
                {data.integration.events.map((e: any) => (
                  <div key={e.topic} className="text-xs">
                    <code>{e.topic}</code> — published by {e.publishers.join(", ") || "?"}, consumed by {e.subscribers.join(", ") || "?"}
                  </div>
                ))}
              </div>
            )}
            {data.integration.core_abstractions.length > 0 && (
              <div>
                <div className="mb-1 text-xs font-semibold uppercase text-slate-400">Core abstractions (most depended-upon)</div>
                {data.integration.core_abstractions.map((c: any) => (
                  <div key={c.file + c.name} className="text-xs">
                    <span className="font-semibold">{c.name}</span> <span className="text-slate-400">used by {c.used_by_files} files</span>
                  </div>
                ))}
              </div>
            )}
            {data.integration.databases.length > 0 && <div className="text-xs">Data stores: {data.integration.databases.join(", ")}</div>}
          </div>
        </Card>
        <Card title="Configuration (environment variables)">
          {data.configuration.length ? (
            <table className="table">
              <tbody>
                {data.configuration.map((v: any) => (
                  <tr key={v.name}>
                    <td className="font-mono text-xs">
                      {v.name} {v.secret && <Badge tone="high">secret</Badge>}
                    </td>
                    <td className="text-xs text-slate-400">{v.files.join(", ")}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : (
            <Empty>No environment variables referenced.</Empty>
          )}
        </Card>
      </div>

      <Card title="Main functionality by module">
        <div className="grid gap-3 md:grid-cols-2">
          {data.main_functionality.map((m: any) => (
            <div key={m.module} className="rounded-lg border border-slate-200 p-3">
              <div className="flex items-center gap-2">
                <span className="font-semibold">{m.module}</span> <Badge>{m.kind}</Badge>
              </div>
              <p className="mt-1 text-sm text-slate-600">{m.summary}</p>
              {m.key_classes.length > 0 && <div className="mt-1 text-xs text-slate-400">Key classes: {m.key_classes.join(", ")}</div>}
            </div>
          ))}
        </div>
      </Card>

      <div className="grid gap-5 lg:grid-cols-[1fr_1fr]">
        <Card title={<span className="flex items-center gap-2"><BookOpen className="h-4 w-4" /> Onboarding guide — where to start</span>}>
          <ol className="space-y-3">
            {data.onboarding.reading_order.map((s: any, i: number) => (
              <li key={s.step}>
                <div className="font-semibold">
                  {i + 1}. {s.step}
                </div>
                <ul className="mt-1 space-y-0.5 pl-4 text-xs text-slate-600">
                  {s.items.map((it: string) => (
                    <li key={it} className="break-all font-mono">{it}</li>
                  ))}
                </ul>
              </li>
            ))}
          </ol>
        </Card>
        <div className="space-y-5">
          <Card title="Most important files">
            <table className="table">
              <thead>
                <tr>
                  <th>File</th>
                  <th>Used by</th>
                  <th>LOC</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {data.onboarding.key_files.map((f: any) => (
                  <tr key={f.file}>
                    <td>
                      <FileRef path={f.file} /> {f.entry && <Badge tone="low">entry</Badge>} {f.apis > 0 && <Badge tone="medium">{f.apis} APIs</Badge>}
                    </td>
                    <td>{f.fan_in}</td>
                    <td>{f.loc}</td>
                    <td>
                      <button className="text-xs text-brand-600 hover:underline" onClick={() => void explain(f.file)}>
                        explain
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </Card>
          <Card title="Domain glossary">
            <div className="flex flex-wrap gap-1.5">
              {data.onboarding.glossary.map((g: any) => (
                <span key={g.term} title={g.defined_in ?? ""} className="rounded-md border border-slate-200 bg-slate-50 px-2 py-0.5 text-sm">
                  {g.term} <span className="text-xs text-slate-400">{g.mentions}</span>
                </span>
              ))}
            </div>
          </Card>
        </div>
      </div>

      <Card title={<span className="flex items-center gap-2"><FileSearch className="h-4 w-4" /> Large file explainer</span>}>
        <div className="grid gap-3 md:grid-cols-[1fr_auto]">
          <FilePicker repoId={repoId} value={path} onChange={(p) => void explain(p)} category="source" />
          <button className="btn-secondary" disabled={!path || explainBusy} onClick={() => void explain(path)}>
            {explainBusy ? <Loader2 className="h-4 w-4 animate-spin" /> : <FileSearch className="h-4 w-4" />} Explain
          </button>
        </div>
        {data.onboarding.large_files.length > 0 && (
          <div className="mt-2 flex flex-wrap gap-1 text-xs">
            <span className="text-slate-400">Largest files:</span>
            {data.onboarding.large_files.map((f: any) => (
              <button key={f.file} className="rounded border border-slate-200 px-1.5 hover:bg-brand-50" onClick={() => void explain(f.file)}>
                {f.file.split("/").pop()} ({f.loc})
              </button>
            ))}
          </div>
        )}
        {explainBusy && <Spinner label="Analyzing file…" />}
        {explained && !explainBusy && (explained.error ? <div className="mt-3"><ErrorBox message={explained.error} /></div> : <FileExplanation data={explained} />)}
      </Card>
    </div>
  );
}

function FileExplanation({ data }: { data: any }) {
  return (
    <div className="mt-4 space-y-4">
      <div>
        <div className="flex flex-wrap items-center gap-2">
          <FileRef path={data.path} /> <Badge tone="medium">{data.role}</Badge> <Badge>{data.language}</Badge> <Badge>{data.loc} LOC</Badge>
        </div>
        <p className="mt-2 text-sm leading-relaxed text-slate-700">{data.summary}</p>
      </div>
      {data.ai_explanation && (
        <div className="markdown rounded-lg border border-brand-100 bg-brand-50/50 p-4 text-sm">
          <ReactMarkdown remarkPlugins={[remarkGfm]}>{data.ai_explanation}</ReactMarkdown>
        </div>
      )}
      <div className="grid gap-4 md:grid-cols-2">
        <div>
          <div className="card-title mb-2">Structure</div>
          <div className="max-h-80 space-y-2 overflow-auto text-xs">
            {data.classes.map((c: any) => (
              <div key={c.name}>
                <div className="font-semibold">
                  {c.kind} {c.name} <span className="text-slate-400">L{c.lines[0]}–{c.lines[1]}</span>
                </div>
                <ul className="pl-4">
                  {c.methods.map((m: any) => (
                    <li key={m.name + m.lines[0]} className="font-mono">
                      {m.name} <span className="text-slate-400">L{m.lines[0]} · cc {m.complexity}</span>
                    </li>
                  ))}
                </ul>
              </div>
            ))}
            {data.functions.map((f: any) => (
              <div key={f.name + f.lines[0]} className="font-mono">
                fn {f.name} <span className="text-slate-400">L{f.lines[0]} · cc {f.complexity}</span>
              </div>
            ))}
          </div>
        </div>
        <div className="space-y-3 text-xs">
          <div>
            <div className="card-title mb-1">Depends on ({data.depends_on.length})</div>
            {data.depends_on.slice(0, 12).map((p: string) => <div key={p} className="font-mono">{p}</div>)}
          </div>
          <div>
            <div className="card-title mb-1">Used by ({data.used_by.length})</div>
            {data.used_by.slice(0, 12).map((p: string) => <div key={p} className="font-mono">{p}</div>)}
          </div>
          {data.endpoints.length > 0 && (
            <div>
              <div className="card-title mb-1">Endpoints</div>
              {data.endpoints.map((e: any) => <div key={e.method + e.path} className="font-mono">{e.method} {e.path}</div>)}
            </div>
          )}
          {data.security_findings.length > 0 && (
            <div>
              <div className="card-title mb-1">Security findings</div>
              {data.security_findings.map((f: any, i: number) => (
                <div key={i}>
                  <Badge tone={f.severity}>{f.severity}</Badge> {f.title} (L{f.line})
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
      {data.refactoring.is_large && (
        <div className="rounded-lg border border-amber-200 bg-amber-50 p-3 text-sm">
          <div className="font-semibold text-amber-800">This file is large — suggested decomposition</div>
          {data.refactoring.split_suggestions.length ? (
            <ul className="mt-1 space-y-1 text-xs">
              {data.refactoring.split_suggestions.map((s: any) => (
                <li key={s.concern}>
                  Extract <code>{s.suggested_class}</code> for “{s.concern}”: {s.methods.join(", ")}
                </li>
              ))}
            </ul>
          ) : (
            <div className="text-xs text-amber-700">Split by responsibility; complexity hotspots: {data.complexity_hotspots.map((h: any) => h.name).join(", ")}</div>
          )}
        </div>
      )}
      {!data.ai_explanation && (
        <CodeBlock title="Complexity hotspots" code={data.complexity_hotspots.map((h: any) => `${h.name.padEnd(48)} cc=${h.complexity}  L${h.lines[0]}-${h.lines[1]}`).join("\n") || "none"} />
      )}
    </div>
  );
}
