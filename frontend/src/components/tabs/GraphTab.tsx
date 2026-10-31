"use client";

import { useEffect, useState } from "react";
import { Search } from "lucide-react";
import { api, useApi } from "@/lib/api";
import type { GraphData, GraphNode } from "@/lib/types";
import { GraphLegend, GraphView, NODE_COLORS } from "@/components/GraphView";
import { Badge, Card, Empty, ErrorBox, Spinner } from "@/components/ui";

type View = "services" | "hierarchy" | "focus";

export function GraphTab({ repoId }: { repoId: string }) {
  const modules = useApi<{ name: string; kind: string; loc: number }[]>(`/repositories/${repoId}/modules`);
  const stats = useApi<any>(`/repositories/${repoId}/graph/stats`);
  const [view, setView] = useState<View>("hierarchy");
  const [module, setModule] = useState("");
  const [focus, setFocus] = useState<string | null>(null);
  const [depth, setDepth] = useState(1);
  const [graph, setGraph] = useState<GraphData | null>(null);
  const [selected, setSelected] = useState<GraphNode | null>(null);
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<GraphNode[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (!module && modules.data?.length) {
      const svc = modules.data.find((m) => m.kind === "service") ?? modules.data[0];
      setModule(svc.name);
    }
  }, [modules.data, module]);

  useEffect(() => {
    const params = new URLSearchParams({ view });
    if (view === "hierarchy") {
      if (!module) return;
      params.set("module", module);
      params.set("limit", "300");
    }
    if (view === "focus") {
      if (!focus) return;
      params.set("node", focus);
      params.set("depth", String(depth));
    }
    setLoading(true);
    setError(null);
    api<GraphData>(`/repositories/${repoId}/graph?${params}`)
      .then(setGraph)
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  }, [repoId, view, module, focus, depth]);

  useEffect(() => {
    if (query.length < 2) {
      setResults([]);
      return;
    }
    const t = setTimeout(() => {
      api<GraphNode[]>(`/repositories/${repoId}/graph/search?q=${encodeURIComponent(query)}`).then(setResults).catch(() => setResults([]));
    }, 250);
    return () => clearTimeout(t);
  }, [query, repoId]);

  function focusOn(id: string) {
    setFocus(id);
    setView("focus");
    setResults([]);
  }

  const labels = Array.from(new Set(graph?.nodes.map((n) => n.label) ?? []));

  return (
    <div className="grid gap-5 lg:grid-cols-[1fr_340px]">
      <Card
        title="Knowledge graph viewer"
        actions={
          <div className="flex flex-wrap items-center gap-2 text-sm">
            <select className="input w-44 py-1" value={view} onChange={(e) => setView(e.target.value as View)}>
              <option value="hierarchy">Repository → Services → APIs → Classes → Functions → Databases</option>
              <option value="services">Services & infrastructure</option>
              <option value="focus" disabled={!focus}>
                Focused neighbourhood
              </option>
            </select>
            {view === "hierarchy" && (
              <select className="input w-48 py-1" value={module} onChange={(e) => setModule(e.target.value)}>
                {modules.data?.map((m) => (
                  <option key={m.name} value={m.name}>
                    {m.name} ({m.kind})
                  </option>
                ))}
              </select>
            )}
            {view === "focus" && (
              <select className="input w-28 py-1" value={depth} onChange={(e) => setDepth(Number(e.target.value))}>
                {[1, 2, 3].map((d) => (
                  <option key={d} value={d}>
                    {d} hop{d > 1 ? "s" : ""}
                  </option>
                ))}
              </select>
            )}
          </div>
        }
      >
        {error && <ErrorBox message={error} />}
        {loading ? (
          <Spinner label="Loading graph…" />
        ) : graph && graph.nodes.length ? (
          <>
            <GraphView data={graph} layout={view === "hierarchy" ? "breadthfirst" : "cose"} height={680} onNodeClick={setSelected} />
            <div className="mt-3 flex items-center justify-between">
              <GraphLegend labels={labels} />
              <span className="text-xs text-slate-400">
                {graph.nodes.length} nodes · {graph.edges.length} edges
              </span>
            </div>
          </>
        ) : (
          <Empty>No nodes for this view.</Empty>
        )}
      </Card>

      <div className="space-y-5">
        <Card title="Search the graph">
          <div className="relative">
            <Search className="absolute left-3 top-2.5 h-4 w-4 text-slate-400" />
            <input className="input pl-9" placeholder="Class, function, API, table…" value={query} onChange={(e) => setQuery(e.target.value)} />
          </div>
          {results.length > 0 && (
            <ul className="mt-2 max-h-72 overflow-auto text-sm">
              {results.map((r) => (
                <li key={r.id} className="cursor-pointer rounded px-2 py-1 hover:bg-brand-50" onClick={() => focusOn(r.id)}>
                  <span className="mr-2 inline-block h-2 w-2 rounded-full" style={{ background: NODE_COLORS[r.label] ?? "#94a3b8" }} />
                  <span className="font-medium">{r.name}</span> <span className="text-xs text-slate-400">{r.label}</span>
                </li>
              ))}
            </ul>
          )}
        </Card>

        <Card title="Node details">
          {selected ? (
            <div className="space-y-2 text-sm">
              <div className="flex items-center gap-2">
                <Badge>{String(selected.type ?? selected.label)}</Badge>
                <span className="font-semibold">{selected.name}</span>
              </div>
              <dl className="space-y-1 text-xs">
                {Object.entries(selected)
                  .filter(([k, v]) => !["id", "name", "label", "type", "display"].includes(k) && v !== null && v !== "" && typeof v !== "object")
                  .map(([k, v]) => (
                    <div key={k} className="flex gap-2">
                      <dt className="w-24 shrink-0 text-slate-400">{k}</dt>
                      <dd className="break-all font-mono">{String(v)}</dd>
                    </div>
                  ))}
              </dl>
              <button className="btn-secondary w-full py-1 text-xs" onClick={() => focusOn(selected.id)}>
                Explore neighbourhood
              </button>
            </div>
          ) : (
            <Empty>Click a node to inspect it.</Empty>
          )}
        </Card>

        <Card title="Graph statistics">
          {stats.data ? (
            <div className="space-y-3 text-sm">
              <div>
                <strong>{stats.data.nodes.toLocaleString()}</strong> nodes · <strong>{stats.data.edges.toLocaleString()}</strong> relationships
              </div>
              <div className="flex flex-wrap gap-1">
                {Object.entries<number>(stats.data.labels).map(([k, v]) => (
                  <Badge key={k}>
                    {k}: {v}
                  </Badge>
                ))}
              </div>
              <div className="flex flex-wrap gap-1">
                {Object.entries<number>(stats.data.relationships).map(([k, v]) => (
                  <Badge key={k} tone="info">
                    {k}: {v}
                  </Badge>
                ))}
              </div>
            </div>
          ) : (
            <Spinner />
          )}
        </Card>
      </div>
    </div>
  );
}
