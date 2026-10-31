"use client";

import { useCallback, useEffect, useState } from "react";
import clsx from "clsx";
import {
  Boxes, Bug, Compass, ExternalLink, FileCheck2, FlaskConical, GitPullRequest, LayoutDashboard, MessageSquare, Network, RefreshCw, Share2, ShieldAlert,
} from "lucide-react";
import { api, post } from "@/lib/api";
import type { RepoSummary } from "@/lib/types";
import { Bar, Card, ErrorBox, ScoreRing, Spinner } from "@/components/ui";
import { OverviewTab } from "@/components/tabs/OverviewTab";
import { PurposeTab } from "@/components/tabs/PurposeTab";
import { ReportTab } from "@/components/tabs/ReportTab";
import { ArchitectureTab } from "@/components/tabs/ArchitectureTab";
import { ChatTab } from "@/components/tabs/ChatTab";
import { SecurityTab } from "@/components/tabs/SecurityTab";
import { PRTab } from "@/components/tabs/PRTab";
import { ImpactTab } from "@/components/tabs/ImpactTab";
import { TestsTab } from "@/components/tabs/TestsTab";
import { GraphTab } from "@/components/tabs/GraphTab";

const TABS = [
  { id: "overview", label: "Overview", icon: LayoutDashboard },
  { id: "report", label: "Deep Report", icon: FileCheck2 },
  { id: "purpose", label: "Purpose & Adoption", icon: Compass },
  { id: "architecture", label: "Architecture", icon: Boxes },
  { id: "chat", label: "Intelligence Chat", icon: MessageSquare },
  { id: "security", label: "Security", icon: ShieldAlert },
  { id: "pr", label: "Pull Requests", icon: GitPullRequest },
  { id: "impact", label: "Impact Analysis", icon: Share2 },
  { id: "tests", label: "Test Intelligence", icon: FlaskConical },
  { id: "graph", label: "Knowledge Graph", icon: Network },
] as const;
type TabId = (typeof TABS)[number]["id"];

const STAGES = ["cloning", "scanning", "parsing", "building knowledge graph", "indexing embeddings", "analyzing", "security analysis", "ready"];

export default function RepoPage({ params }: { params: { id: string } }) {
  const repoId = params.id;
  const [repo, setRepo] = useState<RepoSummary | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [tab, setTab] = useState<TabId>("overview");

  const load = useCallback(async () => {
    try {
      setRepo(await api<RepoSummary>(`/repositories/${repoId}`));
    } catch (e) {
      setError((e as Error).message);
    }
  }, [repoId]);

  useEffect(() => {
    void load();
    const fromHash = window.location.hash.slice(1) as TabId;
    if (TABS.some((t) => t.id === fromHash)) setTab(fromHash);
  }, [load]);

  const inProgress = repo && (repo.status === "queued" || repo.status === "running");
  useEffect(() => {
    if (!inProgress) return;
    const t = setInterval(() => void load(), 2000);
    return () => clearInterval(t);
  }, [inProgress, load]);

  function selectTab(id: TabId) {
    setTab(id);
    history.replaceState(null, "", `#${id}`);
  }

  async function reanalyze() {
    try {
      setRepo(await post<RepoSummary>(`/repositories/${repoId}/reanalyze`, {}));
    } catch (e) {
      setError((e as Error).message);
    }
  }

  if (error && !repo) return <ErrorBox message={error} />;
  if (!repo) return <Spinner />;

  return (
    <div className="space-y-5">
      <div className="card flex flex-wrap items-center gap-6 p-5">
        <div className="min-w-[260px] flex-1">
          <h1 className="break-all text-2xl font-bold text-slate-900">{repo.name}</h1>
          <div className="mt-1 flex flex-wrap items-center gap-3 text-xs text-slate-500">
            {repo.url && (
              <a href={repo.url.replace(/\.git$/, "")} target="_blank" rel="noreferrer noopener" className="flex items-center gap-1 hover:text-brand-600">
                <ExternalLink className="h-3 w-3" /> {repo.url.replace(/\.git$/, "")}
              </a>
            )}
            {repo.branch && <span>branch {repo.branch}</span>}
            {repo.commit_sha && <span className="font-mono">{repo.commit_sha.slice(0, 10)}</span>}
            {repo.updated_at && <span>analyzed {new Date(repo.updated_at).toLocaleString()}</span>}
          </div>
        </div>
        {repo.scores && (
          <div className="flex gap-4">
            <ScoreRing label="Health" score={repo.scores.health} size={72} />
            <ScoreRing label="Architecture" score={repo.scores.architecture} size={72} />
            <ScoreRing label="Security" score={repo.scores.security} size={72} />
            <ScoreRing label="Maintainability" score={repo.scores.maintainability} size={72} />
            <ScoreRing label="Tests" score={repo.scores.test} size={72} />
          </div>
        )}
        <button className="btn-secondary" onClick={() => void reanalyze()} disabled={!!inProgress}>
          <RefreshCw className={clsx("h-4 w-4", inProgress && "animate-spin")} /> Re-analyze
        </button>
      </div>

      {error && <ErrorBox message={error} />}

      {inProgress && (
        <Card title="Analysis in progress">
          <div className="mb-3 flex items-center gap-3 text-sm">
            <Bar value={repo.progress} /> <span className="w-12 text-right font-semibold">{repo.progress}%</span>
          </div>
          <ol className="grid gap-2 text-sm sm:grid-cols-4">
            {STAGES.map((s) => {
              const idx = STAGES.indexOf(s);
              const cur = STAGES.indexOf(repo.stage);
              return (
                <li key={s} className={clsx("rounded-lg border px-3 py-2", idx < cur ? "border-emerald-200 bg-emerald-50 text-emerald-700" : idx === cur ? "border-brand-300 bg-brand-50 font-semibold text-brand-700" : "border-slate-200 text-slate-400")}>
                  {s}
                </li>
              );
            })}
          </ol>
        </Card>
      )}

      {repo.status === "failed" && <ErrorBox message={`Analysis failed: ${repo.error ?? "unknown error"}`} />}

      {repo.status === "ready" && (
        <>
          <nav className="flex flex-wrap gap-1 rounded-xl border border-slate-200 bg-white p-1 shadow-sm">
            {TABS.map(({ id, label, icon: Icon }) => (
              <button
                key={id}
                onClick={() => selectTab(id)}
                className={clsx("flex items-center gap-2 rounded-lg px-3 py-2 text-sm font-medium transition", tab === id ? "bg-brand-600 text-white" : "text-slate-600 hover:bg-slate-100")}
              >
                <Icon className="h-4 w-4" /> {label}
              </button>
            ))}
          </nav>
          <div key={tab}>
            {tab === "overview" && <OverviewTab repoId={repoId} />}
            {tab === "report" && <ReportTab repoId={repoId} />}
            {tab === "purpose" && <PurposeTab repoId={repoId} />}
            {tab === "architecture" && <ArchitectureTab repoId={repoId} />}
            {tab === "chat" && <ChatTab repoId={repoId} />}
            {tab === "security" && <SecurityTab repoId={repoId} />}
            {tab === "pr" && <PRTab repoId={repoId} />}
            {tab === "impact" && <ImpactTab repoId={repoId} />}
            {tab === "tests" && <TestsTab repoId={repoId} />}
            {tab === "graph" && <GraphTab repoId={repoId} />}
          </div>
        </>
      )}
      {repo.status !== "ready" && !inProgress && repo.status !== "failed" && (
        <div className="flex items-center gap-2 text-sm text-slate-500">
          <Bug className="h-4 w-4" /> Repository is {repo.status}.
        </div>
      )}
    </div>
  );
}
