"use client";

import { useApi } from "@/lib/api";
import { Badge, Bar, Card, Empty, ErrorBox, FileRef, LANGUAGE_COLORS, Spinner, Stat } from "@/components/ui";

export function OverviewTab({ repoId }: { repoId: string }) {
  const { data, error, loading } = useApi<any>(`/repositories/${repoId}/overview`);
  if (loading) return <Spinner />;
  if (error) return <ErrorBox message={error} />;
  if (!data) return null;
  const langs = Object.entries<any>(data.languages ?? {});
  const stats = data.stats ?? {};
  const debt = data.scores?.technical_debt;

  return (
    <div className="space-y-5">
      <Card title="Repository summary">
        <p className="text-base leading-relaxed text-slate-700">{data.summary}</p>
        {data.readme_summary && <p className="mt-3 border-l-4 border-brand-200 pl-3 text-sm italic text-slate-500">{data.readme_summary}</p>}
      </Card>

      <div className="grid grid-cols-2 gap-3 md:grid-cols-4 xl:grid-cols-8">
        <Stat label="Files" value={stats.files?.toLocaleString()} hint={`${stats.source_files} source`} />
        <Stat label="Lines of code" value={stats.loc?.toLocaleString()} />
        <Stat label="Classes" value={stats.classes?.toLocaleString()} />
        <Stat label="Functions" value={stats.functions?.toLocaleString()} />
        <Stat label="APIs" value={stats.apis} />
        <Stat label="Data stores" value={stats.databases} hint={`${stats.tables} tables / entities`} />
        <Stat label="Test files" value={stats.test_files} />
        <Stat label="Dependencies" value={stats.dependencies} />
      </div>

      <div className="grid gap-5 lg:grid-cols-2">
        <Card title="Languages">
          <div className="mb-4 flex h-3 overflow-hidden rounded-full">
            {langs.map(([name, v]) => (
              <div key={name} title={`${name} ${v.percent}%`} style={{ width: `${v.percent}%`, background: LANGUAGE_COLORS[name] ?? "#94a3b8" }} />
            ))}
          </div>
          <div className="space-y-2">
            {langs.map(([name, v]) => (
              <div key={name} className="flex items-center gap-3 text-sm">
                <span className="h-3 w-3 rounded-full" style={{ background: LANGUAGE_COLORS[name] ?? "#94a3b8" }} />
                <span className="w-28 font-medium">{name}</span>
                <span className="w-16 text-right text-slate-500">{v.percent}%</span>
                <span className="text-xs text-slate-400">
                  {v.files} files · {v.loc.toLocaleString()} LOC
                </span>
              </div>
            ))}
          </div>
        </Card>
        <Card title="Technology stack">
          <div className="space-y-3">
            {Object.entries<string[]>(data.tech_stack ?? {}).map(([cat, items]) => (
              <div key={cat}>
                <div className="mb-1 text-xs font-semibold uppercase text-slate-400">{cat}</div>
                <div className="flex flex-wrap gap-1.5">
                  {items.map((i) => (
                    <span key={i} className="rounded-md border border-slate-200 bg-slate-50 px-2 py-0.5 text-sm">
                      {i}
                    </span>
                  ))}
                </div>
              </div>
            ))}
          </div>
        </Card>
      </div>

      <Card title={`Module breakdown (${data.modules?.length ?? 0})`}>
        <div className="overflow-x-auto">
          <table className="table">
            <thead>
              <tr>
                <th>Module</th>
                <th>Kind</th>
                <th>Path</th>
                <th className="w-40">Size</th>
                <th>Languages</th>
                <th>APIs</th>
                <th>Depends on</th>
              </tr>
            </thead>
            <tbody>
              {data.modules.map((m: any) => (
                <tr key={m.name}>
                  <td className="font-semibold">{m.name}</td>
                  <td>
                    <Badge tone={m.kind === "service" ? "medium" : undefined}>{m.kind}</Badge>
                  </td>
                  <td>
                    <FileRef path={m.path} />
                  </td>
                  <td>
                    <div className="text-xs text-slate-500">{m.loc.toLocaleString()} LOC · {m.files} files</div>
                    <Bar value={m.loc} max={data.modules[0]?.loc ?? 1} />
                  </td>
                  <td className="text-xs">{Object.keys(m.languages).join(", ")}</td>
                  <td>{m.apis}</td>
                  <td className="text-xs">{m.depends_on.join(", ") || "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>

      <div className="grid gap-5 lg:grid-cols-2">
        <Card title="Frameworks detected">
          {data.frameworks?.length ? (
            <table className="table">
              <thead>
                <tr>
                  <th>Framework</th>
                  <th>Category</th>
                  <th>Evidence</th>
                </tr>
              </thead>
              <tbody>
                {data.frameworks.map((f: any) => (
                  <tr key={f.name}>
                    <td className="font-medium">
                      {f.name} {f.version && <span className="text-xs text-slate-400">{f.version}</span>}
                    </td>
                    <td>
                      <Badge>{f.category}</Badge>
                    </td>
                    <td>
                      <FileRef path={f.evidence} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : (
            <Empty>No frameworks detected.</Empty>
          )}
        </Card>
        <Card title="Entry points">
          {data.entry_points?.length ? (
            <ul className="space-y-2 text-sm">
              {data.entry_points.map((e: any) => (
                <li key={e.file} className="flex flex-wrap items-center gap-2">
                  <FileRef path={e.file} /> <Badge tone="low">{e.type}</Badge> <span className="text-xs text-slate-400">{e.module}</span>
                </li>
              ))}
            </ul>
          ) : (
            <Empty>No entry points detected.</Empty>
          )}
        </Card>
      </div>

      {debt && (
        <Card title={`Technical debt (~${debt.total_hours} engineering hours)`}>
          {debt.items.length ? (
            <div className="grid gap-3 md:grid-cols-2">
              {debt.items.map((d: any) => (
                <div key={d.title} className="rounded-lg border border-slate-200 p-3">
                  <div className="flex items-center justify-between">
                    <span className="font-semibold">{d.title}</span>
                    <Badge tone={d.severity}>
                      {d.count} · {d.effort_hours}h
                    </Badge>
                  </div>
                  <ul className="mt-2 space-y-0.5 text-xs text-slate-500">
                    {d.items.slice(0, 5).map((i: string) => (
                      <li key={i} className="truncate font-mono">
                        {i}
                      </li>
                    ))}
                  </ul>
                </div>
              ))}
            </div>
          ) : (
            <Empty>No significant technical debt detected.</Empty>
          )}
        </Card>
      )}

      {data.pipeline && (
        <div className="text-xs text-slate-400">
          Parser: {data.pipeline.parser} ({Object.entries(data.pipeline.parsers_used ?? {}).map(([k, v]) => `${k}: ${v}`).join(", ")}) · Vector store:{" "}
          {data.pipeline.rag?.backend} · Embeddings: {data.pipeline.rag?.embedder} · {data.pipeline.rag?.chunks} chunks · Neo4j:{" "}
          {data.pipeline.neo4j_synced ? "synced" : "embedded graph"} · {data.pipeline.duration_s}s
        </div>
      )}
    </div>
  );
}
