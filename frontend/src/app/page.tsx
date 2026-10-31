"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { GitBranch, Github, KeyRound, Loader2, Trash2, Upload } from "lucide-react";
import { api, post, useApi } from "@/lib/api";
import type { RepoSummary } from "@/lib/types";
import { Badge, Bar, Card, ErrorBox, scoreColor } from "@/components/ui";

const STATUS_TONE: Record<string, string> = { ready: "low", running: "medium", queued: "info", failed: "critical" };

export default function HomePage() {
  const router = useRouter();
  const repos = useApi<RepoSummary[]>("/repositories");
  const health = useApi<{ capabilities: Record<string, unknown> }>("/health");
  const [url, setUrl] = useState("");
  const [branch, setBranch] = useState("");
  const [token, setToken] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [zip, setZip] = useState<File | null>(null);
  const [zipName, setZipName] = useState("");

  const anyRunning = repos.data?.some((r) => r.status === "running" || r.status === "queued");
  const reloadRepos = repos.reload;
  useEffect(() => {
    if (!anyRunning) return;
    const t = setInterval(() => void reloadRepos(), 3000);
    return () => clearInterval(t);
  }, [anyRunning, reloadRepos]);

  async function analyze(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const repo = await post<RepoSummary>("/repositories", { url, branch: branch || null, token: token || null });
      setToken("");
      router.push(`/repos/${repo.id}`);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function upload() {
    if (!zip) return;
    setBusy(true);
    setError(null);
    try {
      const form = new FormData();
      form.append("file", zip);
      form.append("name", zipName || zip.name.replace(/\.zip$/i, ""));
      const repo = await api<RepoSummary>("/repositories/upload", { method: "POST", body: form });
      router.push(`/repos/${repo.id}`);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function remove(id: string) {
    if (!confirm("Delete this repository and all of its analysis data?")) return;
    try {
      await api(`/repositories/${id}`, { method: "DELETE" });
      void repos.reload();
    } catch (err) {
      setError((err as Error).message);
    }
  }

  return (
    <div className="space-y-6">
      <section className="rounded-2xl bg-gradient-to-br from-slate-900 via-brand-900 to-brand-700 p-8 text-white shadow-lg">
        <h1 className="text-3xl font-bold">Understand any codebase in minutes</h1>
        <p className="mt-2 max-w-3xl text-brand-100">
          Paste a GitHub, GitLab or Bitbucket repository URL. The platform clones it, parses every file with AST analysis, builds a knowledge
          graph and embeddings, and gives you architecture, impact, security, PR and test intelligence with an AI assistant.
        </p>
        <form onSubmit={analyze} className="mt-6 grid gap-3 md:grid-cols-[1fr_180px_220px_auto]">
          <div className="relative">
            <Github className="absolute left-3 top-2.5 h-4 w-4 text-slate-400" />
            <input className="input pl-9 text-slate-900" placeholder="https://github.com/org/repository" value={url} onChange={(e) => setUrl(e.target.value)} required />
          </div>
          <div className="relative">
            <GitBranch className="absolute left-3 top-2.5 h-4 w-4 text-slate-400" />
            <input className="input pl-9 text-slate-900" placeholder="branch (optional)" value={branch} onChange={(e) => setBranch(e.target.value)} />
          </div>
          <div className="relative">
            <KeyRound className="absolute left-3 top-2.5 h-4 w-4 text-slate-400" />
            <input
              className="input pl-9 text-slate-900"
              type="password"
              autoComplete="off"
              placeholder="token for private repos"
              value={token}
              onChange={(e) => setToken(e.target.value)}
            />
          </div>
          <button className="btn-primary bg-amber-500 text-slate-900 hover:bg-amber-400" disabled={busy}>
            {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : null} Analyze repository
          </button>
        </form>
        <p className="mt-2 text-xs text-brand-200">Tokens are used once for cloning and are never stored.</p>
        {health.data && (
          <div className="mt-4 flex flex-wrap gap-2 text-xs">
            {Object.entries(health.data.capabilities).map(([k, v]) => (
              <span key={k} className="rounded-full bg-white/10 px-2 py-0.5">
                {k}: <strong>{v === true ? "enabled" : v === false || v === null ? "off" : String(v)}</strong>
              </span>
            ))}
          </div>
        )}
      </section>

      {error && <ErrorBox message={error} />}

      <div className="grid gap-6 lg:grid-cols-[1fr_340px]">
        <Card title="Repositories">
          {repos.loading && !repos.data ? (
            <div className="text-sm text-slate-500">Loading…</div>
          ) : !repos.data?.length ? (
            <div className="text-sm text-slate-500">No repositories analyzed yet.</div>
          ) : (
            <div className="divide-y divide-slate-100">
              {repos.data.map((r) => (
                <div key={r.id} className="flex items-center gap-4 py-3">
                  <Link href={`/repos/${r.id}`} className="min-w-0 flex-1">
                    <div className="flex items-center gap-2">
                      <span className="truncate font-semibold text-slate-900 hover:text-brand-600">{r.name}</span>
                      <Badge tone={STATUS_TONE[r.status]}>{r.status}</Badge>
                      {r.branch && <span className="text-xs text-slate-400">@{r.branch}</span>}
                    </div>
                    <div className="mt-0.5 truncate text-xs text-slate-500">{r.url ?? "uploaded archive"} · {r.languages.join(", ")}</div>
                    {(r.status === "running" || r.status === "queued") && (
                      <div className="mt-2 flex items-center gap-2 text-xs text-slate-500">
                        <Bar value={r.progress} /> <span className="whitespace-nowrap">{r.stage}</span>
                      </div>
                    )}
                    {r.status === "failed" && <div className="mt-1 line-clamp-2 text-xs text-red-600">{r.error}</div>}
                  </Link>
                  {r.scores && (
                    <div className="text-right">
                      <div className={`text-2xl font-bold ${scoreColor(r.scores.health)}`}>{r.scores.health}</div>
                      <div className="text-[10px] uppercase text-slate-400">health</div>
                    </div>
                  )}
                  <button className="text-slate-400 hover:text-red-600" title="Delete" onClick={() => void remove(r.id)}>
                    <Trash2 className="h-4 w-4" />
                  </button>
                </div>
              ))}
            </div>
          )}
        </Card>

        <Card title="Upload source archive">
          <p className="mb-3 text-sm text-slate-500">No Git access? Upload a .zip of the repository source code.</p>
          <input className="input mb-2" placeholder="Repository name" value={zipName} onChange={(e) => setZipName(e.target.value)} />
          <input type="file" accept=".zip" className="mb-3 block w-full text-sm" onChange={(e) => setZip(e.target.files?.[0] ?? null)} />
          <button className="btn-secondary w-full" disabled={!zip || busy} onClick={() => void upload()}>
            <Upload className="h-4 w-4" /> Upload & analyze
          </button>
        </Card>
      </div>
    </div>
  );
}
