"use client";

import clsx from "clsx";
import { AlertTriangle, Check, Copy, Loader2 } from "lucide-react";
import { useState } from "react";

export function Card({ title, actions, children, className }: { title?: React.ReactNode; actions?: React.ReactNode; children: React.ReactNode; className?: string }) {
  return (
    <section className={clsx("card p-5", className)}>
      {(title || actions) && (
        <div className="mb-4 flex items-center justify-between gap-3">
          {title && <h3 className="card-title">{title}</h3>}
          {actions}
        </div>
      )}
      {children}
    </section>
  );
}

const SEVERITY_STYLES: Record<string, string> = {
  critical: "bg-red-600 text-white",
  high: "bg-orange-500 text-white",
  medium: "bg-amber-100 text-amber-800",
  low: "bg-sky-100 text-sky-800",
  info: "bg-slate-100 text-slate-700",
  direct: "bg-red-100 text-red-700",
  indirect: "bg-amber-100 text-amber-800",
  "via dependency": "bg-violet-100 text-violet-700",
};

export function Badge({ children, tone, className }: { children: React.ReactNode; tone?: string; className?: string }) {
  return (
    <span className={clsx("inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium", SEVERITY_STYLES[tone ?? ""] ?? "bg-slate-100 text-slate-700", className)}>
      {children}
    </span>
  );
}

export function scoreColor(score: number) {
  if (score >= 80) return "text-emerald-600";
  if (score >= 60) return "text-lime-600";
  if (score >= 40) return "text-amber-600";
  return "text-red-600";
}

export function ScoreRing({ label, score, size = 88 }: { label: string; score: number | null | undefined; size?: number }) {
  const value = Math.max(0, Math.min(100, score ?? 0));
  const r = size / 2 - 8;
  const c = 2 * Math.PI * r;
  return (
    <div className="flex flex-col items-center gap-1">
      <svg width={size} height={size} className={scoreColor(value)}>
        <circle cx={size / 2} cy={size / 2} r={r} stroke="#e2e8f0" strokeWidth={8} fill="none" />
        <circle
          cx={size / 2}
          cy={size / 2}
          r={r}
          stroke="currentColor"
          strokeWidth={8}
          fill="none"
          strokeDasharray={c}
          strokeDashoffset={c * (1 - value / 100)}
          strokeLinecap="round"
          transform={`rotate(-90 ${size / 2} ${size / 2})`}
        />
        <text x="50%" y="52%" dominantBaseline="middle" textAnchor="middle" className="fill-slate-800 text-lg font-bold">
          {score ?? "–"}
        </text>
      </svg>
      <span className="text-xs font-medium text-slate-500">{label}</span>
    </div>
  );
}

export function Stat({ label, value, hint }: { label: string; value: React.ReactNode; hint?: string }) {
  return (
    <div className="rounded-lg border border-slate-200 bg-white p-4">
      <div className="text-xs font-medium uppercase tracking-wide text-slate-500">{label}</div>
      <div className="mt-1 text-2xl font-bold text-slate-900">{value}</div>
      {hint && <div className="text-xs text-slate-400">{hint}</div>}
    </div>
  );
}

export function Spinner({ label }: { label?: string }) {
  return (
    <div className="flex items-center gap-2 py-8 text-sm text-slate-500">
      <Loader2 className="h-4 w-4 animate-spin" /> {label ?? "Loading…"}
    </div>
  );
}

export function ErrorBox({ message }: { message: string }) {
  return (
    <div className="flex items-start gap-2 rounded-lg border border-red-200 bg-red-50 p-3 text-sm text-red-700">
      <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" /> {message}
    </div>
  );
}

export function Empty({ children }: { children: React.ReactNode }) {
  return <div className="rounded-lg border border-dashed border-slate-300 p-6 text-center text-sm text-slate-500">{children}</div>;
}

export function CodeBlock({ code, title }: { code: string; title?: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <div className="overflow-hidden rounded-lg border border-slate-800 bg-slate-900">
      <div className="flex items-center justify-between border-b border-slate-700 px-3 py-1.5 text-xs text-slate-400">
        <span className="font-mono">{title}</span>
        <button
          className="flex items-center gap-1 hover:text-white"
          onClick={() => {
            void navigator.clipboard.writeText(code);
            setCopied(true);
            setTimeout(() => setCopied(false), 1500);
          }}
        >
          {copied ? <Check className="h-3.5 w-3.5" /> : <Copy className="h-3.5 w-3.5" />} {copied ? "Copied" : "Copy"}
        </button>
      </div>
      <pre className="max-h-[560px] overflow-auto p-4 font-mono text-xs leading-relaxed text-slate-100">{code}</pre>
    </div>
  );
}

export function Bar({ value, max = 100, className }: { value: number; max?: number; className?: string }) {
  return (
    <div className="h-2 w-full overflow-hidden rounded-full bg-slate-100">
      <div className={clsx("h-full rounded-full", className ?? "bg-brand-500")} style={{ width: `${Math.min(100, (100 * value) / Math.max(1, max))}%` }} />
    </div>
  );
}

export function FileRef({ path, line }: { path: string; line?: number | null }) {
  return (
    <code className="break-all rounded bg-slate-100 px-1 py-0.5 font-mono text-xs text-slate-700">
      {path}
      {line ? `:${line}` : ""}
    </code>
  );
}

export const LANGUAGE_COLORS: Record<string, string> = {
  Java: "#b07219", Python: "#3572A5", TypeScript: "#3178c6", JavaScript: "#f1e05a", "C#": "#178600", Go: "#00ADD8",
  Kotlin: "#A97BFF", Ruby: "#701516", PHP: "#4F5D95", Rust: "#dea584", "C++": "#f34b7d", C: "#555555", Scala: "#c22d40",
  SQL: "#e38c00", Shell: "#89e051", Vue: "#41b883", Swift: "#F05138", Groovy: "#4298b8", Terraform: "#7B42BC",
};
