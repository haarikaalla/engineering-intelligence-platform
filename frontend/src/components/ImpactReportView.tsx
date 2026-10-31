"use client";

import type { ImpactReport } from "@/lib/types";
import { GraphLegend, GraphView } from "@/components/GraphView";
import { Badge, Card, Empty, FileRef, Stat } from "@/components/ui";

export function RiskGauge({ score, level }: { score: number; level: string }) {
  const color = level === "critical" ? "bg-red-600" : level === "high" ? "bg-orange-500" : level === "medium" ? "bg-amber-400" : "bg-emerald-500";
  return (
    <div>
      <div className="flex items-baseline gap-2">
        <span className="text-4xl font-bold text-slate-900">{score}</span>
        <span className="text-sm text-slate-400">/ 100</span>
        <Badge tone={level === "low" ? "low" : level} className="ml-2 uppercase">
          {level} risk
        </Badge>
      </div>
      <div className="mt-2 h-3 w-full overflow-hidden rounded-full bg-slate-100">
        <div className={`h-full ${color}`} style={{ width: `${score}%` }} />
      </div>
    </div>
  );
}

export function ImpactReportView({ report, showGraph = true }: { report: ImpactReport; showGraph?: boolean }) {
  const s = report.summary;
  return (
    <div className="space-y-5">
      <div className="grid grid-cols-2 gap-3 md:grid-cols-6">
        <Stat label="Files" value={s.impacted_files} />
        <Stat label="Services" value={s.impacted_services} />
        <Stat label="APIs" value={s.impacted_apis} />
        <Stat label="Data stores" value={s.impacted_databases} />
        <Stat label="Tests" value={s.impacted_tests} />
        <Stat label="Functions" value={s.impacted_functions} />
      </div>

      <div className="grid gap-5 lg:grid-cols-2">
        <Card title="Impacted services">
          {report.services.length ? (
            <ul className="space-y-2 text-sm">
              {report.services.map((x) => (
                <li key={x.name} className="flex items-center justify-between gap-2">
                  <span className="font-semibold">{x.name}</span>
                  <span className="text-xs text-slate-500">{x.reason}</span>
                </li>
              ))}
            </ul>
          ) : (
            <Empty>None</Empty>
          )}
        </Card>
        <Card title="Impacted APIs">
          {report.apis.length ? (
            <ul className="space-y-1.5 text-sm">
              {report.apis.map((a) => (
                <li key={`${a.module}-${a.name}`} className="flex flex-wrap items-center gap-2">
                  <code className="font-mono text-xs font-semibold">{a.name}</code>
                  <Badge tone={a.impact}>{a.impact}</Badge>
                  <span className="text-xs text-slate-400">{a.handler ?? a.file}</span>
                </li>
              ))}
            </ul>
          ) : (
            <Empty>No API endpoints affected.</Empty>
          )}
        </Card>
        <Card title="Impacted databases & entities">
          {report.databases.length ? (
            <ul className="space-y-1.5 text-sm">
              {report.databases.map((d) => (
                <li key={`${d.type}-${d.name}`} className="flex flex-wrap items-center gap-2">
                  <Badge>{d.type}</Badge> <span className="font-semibold">{d.name}</span> <Badge tone={d.impact}>{d.impact}</Badge>
                  {d.via && <span className="text-xs text-slate-400">via {d.via}</span>}
                </li>
              ))}
            </ul>
          ) : (
            <Empty>No data stores affected.</Empty>
          )}
        </Card>
        <Card title="Impacted tests (run these)">
          {report.tests.length ? (
            <ul className="space-y-1.5 text-sm">
              {report.tests.map((t) => (
                <li key={t.file}>
                  <FileRef path={t.file} /> <span className="text-xs text-slate-400">{t.relation}</span>
                </li>
              ))}
            </ul>
          ) : (
            <Empty>No tests cover the impacted code.</Empty>
          )}
          {!!report.untested_changed_files.length && (
            <div className="mt-3 rounded bg-amber-50 p-2 text-xs text-amber-800">Changed without test coverage: {report.untested_changed_files.join(", ")}</div>
          )}
        </Card>
      </div>

      {showGraph && report.graph.nodes.length > 1 && (
        <Card title="Impact graph" actions={<GraphLegend labels={["Changed", "File", "Test", "API", "Table", "Database"]} />}>
          <GraphView data={report.graph} layout="breadthfirst" height={460} />
        </Card>
      )}

      <Card title="Dependency chains">
        {report.dependency_chains.length ? (
          <div className="space-y-1.5">
            {report.dependency_chains.map((c) => (
              <div key={c.file} className="flex flex-wrap items-center gap-1 text-xs">
                {c.chain.map((step, i) => (
                  <span key={`${step}-${i}`} className="flex items-center gap-1">
                    {i > 0 && <span className="text-slate-400">→</span>}
                    <code className="rounded bg-slate-100 px-1">{step.split("/").pop()}</code>
                  </span>
                ))}
              </div>
            ))}
          </div>
        ) : (
          <Empty>Only direct dependents were found.</Empty>
        )}
        <details className="mt-4">
          <summary className="cursor-pointer text-sm text-slate-500">All impacted files ({report.files.length})</summary>
          <table className="table mt-2">
            <thead>
              <tr>
                <th>File</th>
                <th>Module</th>
                <th>Hops</th>
              </tr>
            </thead>
            <tbody>
              {report.files.map((f) => (
                <tr key={f.file}>
                  <td>
                    <FileRef path={f.file} /> {f.is_test && <Badge tone="low">test</Badge>}
                  </td>
                  <td>{f.module}</td>
                  <td>{f.depth}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </details>
      </Card>
    </div>
  );
}
