"use client";

import { useState } from "react";
import { FlaskConical, Loader2, Wand2 } from "lucide-react";
import { post, useApi } from "@/lib/api";
import { FilePicker, SymbolPicker } from "@/components/FilePicker";
import { Badge, Bar, Card, CodeBlock, Empty, ErrorBox, FileRef, Spinner, Stat } from "@/components/ui";

export function TestsTab({ repoId }: { repoId: string }) {
  const { data, error, loading } = useApi<any>(`/repositories/${repoId}/tests/insights`);
  const [path, setPath] = useState("");
  const [symbol, setSymbol] = useState("");
  const [kind, setKind] = useState<"unit" | "integration">("unit");
  const [useLlm, setUseLlm] = useState(true);
  const [busy, setBusy] = useState(false);
  const [genError, setGenError] = useState<string | null>(null);
  const [generated, setGenerated] = useState<any>(null);

  async function generate(p = path, k = kind, s = symbol) {
    setBusy(true);
    setGenError(null);
    try {
      setGenerated(await post(`/repositories/${repoId}/tests/generate`, { path: p, symbol: s || null, kind: k, use_llm: useLlm }));
    } catch (e) {
      setGenError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  if (loading) return <Spinner />;
  if (error) return <ErrorBox message={error} />;
  if (!data) return null;
  const cov = data.coverage_estimate;

  return (
    <div className="space-y-5">
      <div className="grid grid-cols-2 gap-3 md:grid-cols-5">
        <Stat label="Files with tests" value={`${cov.files_percent}%`} hint={`${cov.tested_files}/${cov.testable_files} testable files`} />
        <Stat label="LOC with tests" value={`${cov.loc_percent}%`} />
        <Stat label="Test files" value={cov.test_files} />
        <Stat label="Untested high-risk" value={data.recommendations.filter((r: any) => r.priority === "high").length} />
        <Stat label="Frameworks" value={<span className="text-base">{data.frameworks.join(", ") || "—"}</span>} />
      </div>

      <Card title="Generate tests">
        <div className="grid gap-3 md:grid-cols-[1fr_260px_160px_auto]">
          <FilePicker repoId={repoId} value={path} category="source" onChange={(p) => { setPath(p); setSymbol(""); }} />
          <SymbolPicker repoId={repoId} path={path} value={symbol} onChange={setSymbol} />
          <select className="input" value={kind} onChange={(e) => setKind(e.target.value as "unit" | "integration")}>
            <option value="unit">Unit tests</option>
            <option value="integration">Integration tests</option>
          </select>
          <button className="btn-primary" disabled={!path || busy} onClick={() => void generate()}>
            {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : <Wand2 className="h-4 w-4" />} Generate
          </button>
        </div>
        <label className="mt-2 flex items-center gap-2 text-xs text-slate-500">
          <input type="checkbox" checked={useLlm} onChange={(e) => setUseLlm(e.target.checked)} /> Use LLM when configured (falls back to framework-aware templates)
        </label>
        {genError && <div className="mt-3"><ErrorBox message={genError} /></div>}
        {generated && (
          <div className="mt-4 space-y-2">
            <div className="flex flex-wrap gap-2 text-xs">
              <Badge>{generated.language}</Badge>
              <Badge>{generated.framework}</Badge>
              <Badge tone="low">{generated.mode}</Badge>
              {generated.notes && <span className="text-amber-600">{generated.notes}</span>}
            </div>
            <CodeBlock code={generated.code} title={generated.file_name} />
          </div>
        )}
      </Card>

      <Card title="Coverage recommendations">
        {data.recommendations.length ? (
          <table className="table">
            <thead>
              <tr>
                <th>File</th>
                <th>Priority</th>
                <th>Suggested</th>
                <th>Why</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {data.recommendations.map((r: any) => (
                <tr key={r.file}>
                  <td>
                    <FileRef path={r.file} />
                  </td>
                  <td>
                    <Badge tone={r.priority}>{r.priority}</Badge>
                  </td>
                  <td>{r.test_type}</td>
                  <td className="text-xs text-slate-500">{r.reason}</td>
                  <td>
                    <button
                      className="btn-secondary py-1 text-xs"
                      disabled={busy}
                      onClick={() => {
                        setPath(r.file);
                        setSymbol("");
                        setKind(r.test_type);
                        void generate(r.file, r.test_type, "");
                        window.scrollTo({ top: 0, behavior: "smooth" });
                      }}
                    >
                      <FlaskConical className="h-3.5 w-3.5" /> Generate
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          <Empty>Every testable file already has associated tests.</Empty>
        )}
      </Card>

      <Card title="Regression risk analysis">
        <table className="table">
          <thead>
            <tr>
              <th>File</th>
              <th className="w-48">Risk</th>
              <th>Complexity</th>
              <th>Fan-in</th>
              <th>Commits</th>
              <th>Tests</th>
            </tr>
          </thead>
          <tbody>
            {data.regression_risk.map((r: any) => (
              <tr key={r.file}>
                <td>
                  <FileRef path={r.file} />
                  <div className="mt-0.5 flex gap-1">
                    {r.has_api && <Badge tone="medium">API</Badge>}
                    {r.has_db && <Badge tone="info">DB</Badge>}
                  </div>
                </td>
                <td>
                  <div className="text-xs">{r.risk}</div>
                  <Bar value={r.risk} className={r.risk >= 50 ? "bg-red-500" : r.risk >= 25 ? "bg-amber-400" : "bg-emerald-500"} />
                </td>
                <td>{r.complexity}</td>
                <td>{r.fan_in}</td>
                <td>{r.commits}</td>
                <td className="text-xs">{r.tests.length ? r.tests.map((t: string) => t.split("/").pop()).join(", ") : <span className="text-red-500">none</span>}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </Card>
    </div>
  );
}
