"use client";

import { useState } from "react";
import { Loader2, Share2 } from "lucide-react";
import { post } from "@/lib/api";
import type { ImpactReport } from "@/lib/types";
import { FilePicker, SymbolPicker } from "@/components/FilePicker";
import { ImpactReportView, RiskGauge } from "@/components/ImpactReportView";
import { Card, ErrorBox } from "@/components/ui";

export function ImpactTab({ repoId }: { repoId: string }) {
  const [path, setPath] = useState("");
  const [symbol, setSymbol] = useState("");
  const [depth, setDepth] = useState(4);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [report, setReport] = useState<ImpactReport | null>(null);

  async function run() {
    setBusy(true);
    setError(null);
    try {
      setReport(await post<ImpactReport>(`/repositories/${repoId}/impact`, { path, symbol: symbol || null, max_depth: depth }));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-5">
      <Card title="Change impact analysis">
        <div className="grid gap-3 md:grid-cols-[1fr_280px_120px_auto]">
          <FilePicker repoId={repoId} value={path} onChange={(p) => { setPath(p); setSymbol(""); }} />
          <SymbolPicker repoId={repoId} path={path} value={symbol} onChange={setSymbol} />
          <select className="input" value={depth} onChange={(e) => setDepth(Number(e.target.value))}>
            {[1, 2, 3, 4, 5, 6].map((d) => (
              <option key={d} value={d}>
                depth {d}
              </option>
            ))}
          </select>
          <button className="btn-primary" disabled={!path || busy} onClick={() => void run()}>
            {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : <Share2 className="h-4 w-4" />} Analyze impact
          </button>
        </div>
        <p className="mt-2 text-xs text-slate-500">
          Select a changed file (optionally a specific class or function). The engine walks reverse IMPORTS / REFERENCES / CALLS edges of the
          knowledge graph and maps the blast radius to services, APIs, data stores and tests.
        </p>
      </Card>
      {error && <ErrorBox message={error} />}
      {report && (
        <>
          <Card title={`Risk for ${report.changed.join(", ")}`}>
            <RiskGauge score={report.risk.score} level={report.risk.level} />
          </Card>
          <ImpactReportView report={report} />
        </>
      )}
    </div>
  );
}
