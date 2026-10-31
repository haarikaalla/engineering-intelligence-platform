"use client";

import { useEffect, useRef, useState } from "react";
import { X } from "lucide-react";
import { api } from "@/lib/api";
import { Spinner } from "@/components/ui";

export interface Evidence {
  path: string;
  line: number;
  end_line?: number;
}

/** Shows the exact source lines behind a fact so every claim can be checked. */
export function CodeViewer({ repoId, evidence, onClose }: { repoId: string; evidence: Evidence; onClose: () => void }) {
  const [content, setContent] = useState<string[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const target = useRef<HTMLDivElement>(null);
  const start = evidence.line;
  const end = Math.max(evidence.end_line ?? start, start);

  useEffect(() => {
    api<{ content: string }>(`/repositories/${repoId}/file?path=${encodeURIComponent(evidence.path)}`)
      .then((r) => setContent(r.content.split("\n")))
      .catch((e) => setError(e.message));
  }, [repoId, evidence.path]);

  useEffect(() => {
    target.current?.scrollIntoView({ block: "center" });
  }, [content]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/60 p-6" onClick={onClose}>
      <div className="flex max-h-[85vh] w-full max-w-5xl flex-col overflow-hidden rounded-xl bg-slate-900 shadow-2xl" onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center justify-between border-b border-slate-700 px-4 py-2 text-sm text-slate-200">
          <span className="font-mono">
            {evidence.path}:{start}
            {end > start ? `-${end}` : ""}
          </span>
          <button onClick={onClose} className="text-slate-400 hover:text-white" aria-label="Close">
            <X className="h-4 w-4" />
          </button>
        </div>
        <div className="overflow-auto font-mono text-xs leading-5">
          {error && <div className="p-4 text-red-300">{error}</div>}
          {!content && !error && <div className="p-4"><Spinner /></div>}
          {content?.map((line, i) => {
            const n = i + 1;
            const hit = n >= start && n <= Math.min(end, start + 400);
            return (
              <div key={n} ref={n === start ? target : undefined} className={hit ? "bg-amber-400/20" : ""}>
                <span className="inline-block w-12 select-none pr-3 text-right text-slate-500">{n}</span>
                <span className="whitespace-pre text-slate-100">{line}</span>
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}

export function EvidenceChip({ e, onOpen }: { e: Evidence; onOpen: (e: Evidence) => void }) {
  const name = e.path.split("/").pop();
  return (
    <button
      title={`${e.path}:${e.line}`}
      onClick={() => onOpen(e)}
      className="rounded border border-slate-200 bg-slate-50 px-1.5 py-0.5 font-mono text-[11px] text-slate-600 hover:border-brand-300 hover:bg-brand-50 hover:text-brand-700"
    >
      {name}:{e.line}
    </button>
  );
}
