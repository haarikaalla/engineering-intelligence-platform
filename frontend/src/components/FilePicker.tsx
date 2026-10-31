"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";

interface FileRow {
  path: string;
  language: string;
  category: string;
  module: string;
}

export function FilePicker({ repoId, value, onChange, category }: { repoId: string; value: string; onChange: (p: string) => void; category?: string }) {
  const [query, setQuery] = useState(value);
  const [options, setOptions] = useState<FileRow[]>([]);
  const [open, setOpen] = useState(false);

  useEffect(() => setQuery(value), [value]);

  useEffect(() => {
    if (!open) return;
    const handle = setTimeout(async () => {
      const params = new URLSearchParams({ q: query, limit: "40" });
      if (category) params.set("category", category);
      try {
        setOptions(await api<FileRow[]>(`/repositories/${repoId}/files?${params}`));
      } catch {
        setOptions([]);
      }
    }, 200);
    return () => clearTimeout(handle);
  }, [query, open, repoId, category]);

  return (
    <div className="relative">
      <input
        className="input font-mono"
        placeholder="Search a file, e.g. OrderService.java"
        value={query}
        onFocus={() => setOpen(true)}
        onBlur={() => setTimeout(() => setOpen(false), 150)}
        onChange={(e) => setQuery(e.target.value)}
      />
      {open && options.length > 0 && (
        <ul className="absolute z-20 mt-1 max-h-72 w-full overflow-auto rounded-lg border border-slate-200 bg-white text-sm shadow-lg">
          {options.map((o) => (
            <li
              key={o.path}
              className="cursor-pointer px-3 py-1.5 hover:bg-brand-50"
              onMouseDown={() => {
                onChange(o.path);
                setQuery(o.path);
                setOpen(false);
              }}
            >
              <div className="truncate font-mono text-xs">{o.path}</div>
              <div className="text-[11px] text-slate-400">
                {o.language} · {o.category} · {o.module}
              </div>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

export function SymbolPicker({ repoId, path, value, onChange }: { repoId: string; path: string; value: string; onChange: (s: string) => void }) {
  const [symbols, setSymbols] = useState<{ name: string; kind: string; line: number }[]>([]);
  useEffect(() => {
    if (!path) {
      setSymbols([]);
      return;
    }
    api(`/repositories/${repoId}/file?path=${encodeURIComponent(path)}`)
      .then((r) => setSymbols(r.symbols ?? []))
      .catch(() => setSymbols([]));
  }, [repoId, path]);
  return (
    <select className="input" value={value} onChange={(e) => onChange(e.target.value)} disabled={!symbols.length}>
      <option value="">Entire file</option>
      {symbols.map((s) => (
        <option key={`${s.name}:${s.line}`} value={s.name}>
          {s.kind} {s.name} (L{s.line})
        </option>
      ))}
    </select>
  );
}
