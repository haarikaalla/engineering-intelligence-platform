"use client";

import { Fragment, useMemo, useState } from "react";
import { ChevronDown, ChevronRight } from "lucide-react";
import { useApi } from "@/lib/api";
import type { Finding } from "@/lib/types";
import { Badge, Card, Empty, ErrorBox, FileRef, ScoreRing, Spinner, Stat } from "@/components/ui";

const SEVERITIES = ["critical", "high", "medium", "low"];

export function SecurityTab({ repoId }: { repoId: string }) {
  const { data, error, loading } = useApi<any>(`/repositories/${repoId}/security`);
  const [severity, setSeverity] = useState("");
  const [category, setCategory] = useState("");
  const [hideLow, setHideLow] = useState(true);
  const [open, setOpen] = useState<number | null>(null);

  const findings: Finding[] = useMemo(
    () =>
      (data?.findings ?? []).filter(
        (f: Finding) => (!severity || f.severity === severity) && (!category || f.category === category) && (!hideLow || f.confidence !== "low"),
      ),
    [data, severity, category, hideLow],
  );
  if (loading) return <Spinner />;
  if (error) return <ErrorBox message={error} />;
  if (!data) return null;
  const bySev = data.summary.by_severity ?? {};
  const deps = data.dependencies ?? {};

  return (
    <div className="space-y-5">
      <div className="grid gap-4 md:grid-cols-[auto_1fr]">
        <Card>
          <ScoreRing label="Security score" score={data.score} size={120} />
        </Card>
        <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">
          {SEVERITIES.map((s) => (
            <Stat key={s} label={s} value={bySev[s] ?? 0} />
          ))}
          <Stat label="Vulnerable deps" value={data.summary.dependency_vulnerabilities} hint={`source: ${deps.source}`} />
          <Stat label="Unpinned deps" value={deps.unpinned ?? 0} hint={`${deps.total_dependencies ?? 0} total`} />
        </div>
      </div>

      <Card
        title={`Findings (${findings.length})`}
        actions={
          <div className="flex flex-wrap items-center gap-2 text-sm">
            <select className="input w-36 py-1" value={severity} onChange={(e) => setSeverity(e.target.value)}>
              <option value="">All severities</option>
              {SEVERITIES.map((s) => (
                <option key={s}>{s}</option>
              ))}
            </select>
            <select className="input w-40 py-1" value={category} onChange={(e) => setCategory(e.target.value)}>
              <option value="">All categories</option>
              {Object.keys(data.summary.by_category ?? {}).map((c) => (
                <option key={c}>{c}</option>
              ))}
            </select>
            <label className="flex items-center gap-1 text-xs text-slate-500">
              <input type="checkbox" checked={hideLow} onChange={(e) => setHideLow(e.target.checked)} /> hide low-confidence (tests/docs)
            </label>
          </div>
        }
      >
        {findings.length ? (
          <div className="overflow-x-auto">
            <table className="table">
              <thead>
                <tr>
                  <th />
                  <th>Severity</th>
                  <th>Issue</th>
                  <th>Category</th>
                  <th>Location</th>
                </tr>
              </thead>
              <tbody>
                {findings.slice(0, 500).map((f, i) => (
                  <Fragment key={`${f.rule_id}-${f.file}-${f.line}-${i}`}>
                    <tr className="cursor-pointer hover:bg-slate-50" onClick={() => setOpen(open === i ? null : i)}>
                      <td>{open === i ? <ChevronDown className="h-4 w-4" /> : <ChevronRight className="h-4 w-4" />}</td>
                      <td>
                        <Badge tone={f.severity}>{f.severity}</Badge>
                      </td>
                      <td>
                        <div className="font-medium">{f.title}</div>
                        <div className="text-xs text-slate-400">
                          {f.rule_id} · {f.cwe}
                        </div>
                      </td>
                      <td>
                        <Badge>{f.category}</Badge>
                      </td>
                      <td>
                        <FileRef path={f.file} line={f.line} />
                      </td>
                    </tr>
                    {open === i && (
                      <tr>
                        <td />
                        <td colSpan={4}>
                          <pre className="overflow-x-auto rounded bg-slate-900 p-2 font-mono text-xs text-slate-100">{f.snippet}</pre>
                          <div className="mt-2 text-sm text-emerald-700">Fix: {f.recommendation}</div>
                        </td>
                      </tr>
                    )}
                  </Fragment>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <Empty>No findings for the selected filters.</Empty>
        )}
      </Card>

      <Card title={`Dependency security analysis (${deps.vulnerabilities?.length ?? 0} vulnerabilities)`}>
        {deps.vulnerabilities?.length ? (
          <table className="table">
            <thead>
              <tr>
                <th>Severity</th>
                <th>Package</th>
                <th>Advisory</th>
                <th>Fixed in</th>
                <th>Manifest</th>
              </tr>
            </thead>
            <tbody>
              {deps.vulnerabilities.map((v: any, i: number) => (
                <tr key={`${v.id}-${v.package}-${i}`}>
                  <td>
                    <Badge tone={v.severity}>{v.severity}</Badge>
                  </td>
                  <td>
                    <div className="font-mono text-xs">{v.package}</div>
                    <div className="text-xs text-slate-400">
                      {v.ecosystem} {v.version}
                    </div>
                  </td>
                  <td>
                    <div className="font-medium">{v.id}</div>
                    <div className="text-xs text-slate-500">{v.summary}</div>
                  </td>
                  <td className="font-mono text-xs">{v.fixed_in ?? "—"}</td>
                  <td>
                    <FileRef path={v.file} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          <Empty>No known vulnerable dependency versions found ({deps.pinned ?? 0} pinned dependencies checked).</Empty>
        )}
      </Card>

      <div className="grid gap-5 lg:grid-cols-2">
        <Card title="Riskiest files">
          {data.risky_files.length ? (
            <ul className="space-y-1">
              {data.risky_files.map((r: any) => (
                <li key={r.file} className="flex items-center justify-between gap-2">
                  <FileRef path={r.file} /> <Badge tone="high">{r.findings}</Badge>
                </li>
              ))}
            </ul>
          ) : (
            <Empty>None</Empty>
          )}
        </Card>
        <Card title="Authentication & security surface">
          {data.auth_surface.length ? (
            <ul className="space-y-1">
              {data.auth_surface.map((p: string) => (
                <li key={p}>
                  <FileRef path={p} />
                </li>
              ))}
            </ul>
          ) : (
            <Empty>No authentication-related code detected.</Empty>
          )}
        </Card>
      </div>
    </div>
  );
}
