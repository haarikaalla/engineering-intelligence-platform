"use client";

import { useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { GitPullRequest, Loader2 } from "lucide-react";
import { post } from "@/lib/api";
import { ImpactReportView, RiskGauge } from "@/components/ImpactReportView";
import { Badge, Card, Empty, ErrorBox, FileRef, Stat } from "@/components/ui";

export function PRTab({ repoId }: { repoId: string }) {
  const [mode, setMode] = useState<"url" | "diff">("url");
  const [prUrl, setPrUrl] = useState("");
  const [token, setToken] = useState("");
  const [diff, setDiff] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<any>(null);

  async function run() {
    setBusy(true);
    setError(null);
    setResult(null);
    try {
      const body = mode === "url" ? { pr_url: prUrl, token: token || null } : { diff };
      setResult(await post(`/repositories/${repoId}/pr`, body));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  const byCategory: Record<string, any[]> = {};
  for (const s of result?.suggestions ?? []) (byCategory[s.category] ??= []).push(s);

  return (
    <div className="space-y-5">
      <Card title="Pull request intelligence">
        <div className="mb-3 flex gap-2">
          <button className={mode === "url" ? "btn-primary py-1" : "btn-secondary py-1"} onClick={() => setMode("url")}>
            GitHub PR URL
          </button>
          <button className={mode === "diff" ? "btn-primary py-1" : "btn-secondary py-1"} onClick={() => setMode("diff")}>
            Paste unified diff
          </button>
        </div>
        {mode === "url" ? (
          <div className="grid gap-3 md:grid-cols-[1fr_260px]">
            <input className="input" placeholder="https://github.com/org/repo/pull/123" value={prUrl} onChange={(e) => setPrUrl(e.target.value)} />
            <input className="input" type="password" autoComplete="off" placeholder="token (private repos)" value={token} onChange={(e) => setToken(e.target.value)} />
          </div>
        ) : (
          <textarea className="input h-56 font-mono text-xs" placeholder="Output of `git diff main...feature`" value={diff} onChange={(e) => setDiff(e.target.value)} />
        )}
        <button className="btn-primary mt-3" disabled={busy || (mode === "url" ? !prUrl : !diff)} onClick={() => void run()}>
          {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : <GitPullRequest className="h-4 w-4" />} Review pull request
        </button>
      </Card>

      {error && <ErrorBox message={error} />}

      {result && (
        <>
          <div className="grid gap-5 lg:grid-cols-[1fr_1fr]">
            <Card title={result.meta?.title ? `Risk score — ${result.meta.title}` : "Risk score"}>
              <RiskGauge score={result.risk.score} level={result.risk.level} />
              <ul className="mt-4 space-y-1 text-sm">
                {result.risk.factors.map((f: any) => (
                  <li key={f.reason} className="flex justify-between gap-2">
                    <span>{f.reason}</span>
                    <span className="font-mono text-slate-500">+{f.points}</span>
                  </li>
                ))}
              </ul>
            </Card>
            <Card title="Change summary">
              <div className="grid grid-cols-3 gap-3">
                <Stat label="Files" value={result.stats.files} />
                <Stat label="Additions" value={`+${result.stats.additions}`} />
                <Stat label="Deletions" value={`-${result.stats.deletions}`} />
              </div>
              <ul className="mt-3 max-h-48 space-y-1 overflow-auto text-xs">
                {result.files.map((f: any) => (
                  <li key={f.path} className="flex items-center gap-2">
                    <Badge>{f.status}</Badge> <FileRef path={f.path} />
                    <span className="text-emerald-600">+{f.additions}</span>
                    <span className="text-red-600">-{f.deletions}</span>
                  </li>
                ))}
              </ul>
            </Card>
          </div>

          {result.ai_review && (
            <Card title="AI review">
              <div className="markdown text-sm">
                <ReactMarkdown remarkPlugins={[remarkGfm]}>{result.ai_review}</ReactMarkdown>
              </div>
            </Card>
          )}

          <Card title={`Review suggestions (${result.suggestions.length})`}>
            {result.suggestions.length ? (
              <div className="space-y-4">
                {Object.entries(byCategory).map(([cat, items]) => (
                  <div key={cat}>
                    <div className="mb-1 text-xs font-semibold uppercase text-slate-400">{cat}</div>
                    <ul className="space-y-1.5">
                      {items.map((s: any, i: number) => (
                        <li key={i} className="flex flex-wrap items-start gap-2 text-sm">
                          <Badge tone={s.severity}>{s.severity}</Badge>
                          <FileRef path={s.file} line={s.line} />
                          <span className="flex-1">{s.comment}</span>
                        </li>
                      ))}
                    </ul>
                  </div>
                ))}
              </div>
            ) : (
              <Empty>No issues found — looks good.</Empty>
            )}
          </Card>

          <div className="grid gap-5 lg:grid-cols-3">
            <Card title="Security impact">
              {result.security.findings.length ? (
                <ul className="space-y-1.5 text-sm">
                  {result.security.findings.map((f: any, i: number) => (
                    <li key={i}>
                      <Badge tone={f.severity}>{f.severity}</Badge> {f.title}
                      <div>
                        <FileRef path={f.file} line={f.line} />
                      </div>
                    </li>
                  ))}
                </ul>
              ) : (
                <Empty>No security issues in added code.</Empty>
              )}
            </Card>
            <Card title="Architecture impact">
              {result.architecture.length ? (
                <ul className="space-y-1.5 text-sm">
                  {result.architecture.map((n: any, i: number) => (
                    <li key={i}>
                      <Badge tone={n.severity}>{n.type.replaceAll("_", " ")}</Badge>
                      <div className="text-xs text-slate-600">{n.detail}</div>
                    </li>
                  ))}
                </ul>
              ) : (
                <Empty>No architectural changes detected.</Empty>
              )}
            </Card>
            <Card title="Missing tests">
              {result.missing_tests.length ? (
                <ul className="space-y-1.5 text-sm">
                  {result.missing_tests.map((m: any) => (
                    <li key={m.file}>
                      <FileRef path={m.file} />
                      <div className="text-xs text-slate-500">{m.suggestion}</div>
                    </li>
                  ))}
                </ul>
              ) : (
                <Empty>All changed source files have accompanying test changes.</Empty>
              )}
            </Card>
          </div>

          {result.impact && (
            <div>
              <h3 className="card-title mb-3">Change impact</h3>
              <ImpactReportView report={result.impact} />
            </div>
          )}
        </>
      )}
    </div>
  );
}
