"use client";

import { useApi } from "@/lib/api";
import type { GraphData } from "@/lib/types";
import { GraphLegend, GraphView } from "@/components/GraphView";
import { Mermaid } from "@/components/Mermaid";
import { Badge, Bar, Card, Empty, ErrorBox, FileRef, Spinner } from "@/components/ui";

export function ArchitectureTab({ repoId }: { repoId: string }) {
  const { data, error, loading } = useApi<any>(`/repositories/${repoId}/architecture`);
  const graph = useApi<GraphData>(`/repositories/${repoId}/graph?view=services`);
  if (loading) return <Spinner />;
  if (error) return <ErrorBox message={error} />;
  if (!data) return null;
  const layers = Object.entries<number>(data.layers ?? {});
  const maxLayer = Math.max(1, ...layers.map(([, v]) => v));

  return (
    <div className="space-y-5">
      <div className="grid gap-5 lg:grid-cols-[1fr_380px]">
        <Card title="Architecture style">
          <div className="text-xl font-bold text-slate-900">{data.style.primary}</div>
          <div className="mt-2 flex flex-wrap gap-2">
            {data.style.traits.map((t: string) => (
              <Badge key={t} tone="low">
                {t}
              </Badge>
            ))}
          </div>
          <p className="mt-4 text-sm leading-relaxed text-slate-600">{data.business_flow_summary}</p>
        </Card>
        <Card title="Layers (source files)">
          <div className="space-y-2">
            {layers.map(([name, count]) => (
              <div key={name} className="text-sm">
                <div className="flex justify-between">
                  <span className="capitalize">{name}</span>
                  <span className="text-slate-500">{count}</span>
                </div>
                <Bar value={count} max={maxLayer} />
              </div>
            ))}
          </div>
        </Card>
      </div>

      <Card title="Architecture visualization">
        <Mermaid chart={data.diagrams.architecture} />
      </Card>

      <Card title="Service relationships & dependency graph" actions={<GraphLegend labels={["Repository", "Service", "Module", "Database", "Topic", "Infrastructure"]} />}>
        {graph.loading ? <Spinner /> : graph.data ? <GraphView data={graph.data} height={520} showEdgeLabels /> : <Empty>No graph</Empty>}
      </Card>

      <div className="grid gap-5 lg:grid-cols-2">
        <Card title="Services">
          {data.services.length ? (
            <table className="table">
              <thead>
                <tr>
                  <th>Service</th>
                  <th>LOC</th>
                  <th>APIs</th>
                  <th>Fan-in</th>
                  <th>Fan-out</th>
                </tr>
              </thead>
              <tbody>
                {data.services.map((s: any) => (
                  <tr key={s.name}>
                    <td>
                      <div className="font-semibold">{s.name}</div>
                      <div className="text-xs text-slate-400">{s.entry_points[0]}</div>
                    </td>
                    <td>{s.loc.toLocaleString()}</td>
                    <td>{s.apis}</td>
                    <td>{s.fan_in}</td>
                    <td>{s.fan_out}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : (
            <Empty>No deployable services detected.</Empty>
          )}
        </Card>
        <Card title="Service dependency map">
          {data.dependency_map.length ? (
            <table className="table">
              <thead>
                <tr>
                  <th>From</th>
                  <th>To</th>
                  <th>Strength</th>
                  <th>Via</th>
                </tr>
              </thead>
              <tbody>
                {data.dependency_map.map((d: any) => (
                  <tr key={`${d.source}-${d.target}`}>
                    <td className="font-medium">{d.source}</td>
                    <td className="font-medium">{d.target}</td>
                    <td>{d.weight}</td>
                    <td>
                      <Badge>{d.via}</Badge>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : (
            <Empty>No cross-module dependencies.</Empty>
          )}
        </Card>
      </div>

      <Card title="Data flow diagram (API → code → data store)">
        <Mermaid chart={data.diagrams.data_flow} />
        <div className="mt-4 space-y-2">
          {data.data_flows.slice(0, 12).map((f: any) => (
            <div key={f.api} className="flex flex-wrap items-center gap-1 text-xs">
              {f.steps.map((s: any, i: number) => (
                <span key={`${s.id}-${i}`} className="flex items-center gap-1">
                  {i > 0 && <span className="text-slate-400">→</span>}
                  <span className="rounded border border-slate-200 bg-slate-50 px-1.5 py-0.5">
                    <span className="text-slate-400">{s.label}:</span> {s.name}
                  </span>
                </span>
              ))}
            </div>
          ))}
        </div>
      </Card>

      <div className="grid gap-5 lg:grid-cols-2">
        <Card title="Business capabilities">
          {data.business_capabilities.length ? (
            <table className="table">
              <thead>
                <tr>
                  <th>Capability</th>
                  <th>Endpoints</th>
                  <th>Methods</th>
                  <th>Owned by</th>
                </tr>
              </thead>
              <tbody>
                {data.business_capabilities.map((c: any) => (
                  <tr key={c.name}>
                    <td className="font-semibold capitalize">{c.name}</td>
                    <td>{c.endpoints}</td>
                    <td className="text-xs">{Object.entries(c.methods).map(([m, n]) => `${m}×${n}`).join(" ")}</td>
                    <td className="text-xs">{c.modules.join(", ")}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : (
            <Empty>No API capabilities detected.</Empty>
          )}
        </Card>
        <Card title="Asynchronous messaging">
          {data.messaging.length ? (
            <table className="table">
              <thead>
                <tr>
                  <th>Topic / queue</th>
                  <th>Publishers</th>
                  <th>Subscribers</th>
                </tr>
              </thead>
              <tbody>
                {data.messaging.map((m: any) => (
                  <tr key={m.topic}>
                    <td className="font-mono text-xs">{m.topic}</td>
                    <td>{m.publishers.join(", ") || "—"}</td>
                    <td>{m.subscribers.join(", ") || "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : (
            <Empty>No messaging detected.</Empty>
          )}
        </Card>
      </div>

      <div className="grid gap-5 lg:grid-cols-3">
        <Card title="Dependency cycles">
          {data.module_cycles.length || data.file_cycle_count ? (
            <div className="space-y-2 text-sm">
              {data.module_cycles.map((c: string[]) => (
                <div key={c.join()} className="rounded bg-red-50 p-2 text-red-700">
                  Module cycle: {c.join(" ↔ ")}
                </div>
              ))}
              <div className="text-slate-500">{data.file_cycle_count} circular file import group(s)</div>
              {data.file_cycles.slice(0, 5).map((c: string[]) => (
                <div key={c.join()} className="truncate font-mono text-xs text-slate-500" title={c.join(" → ")}>
                  {c.join(" → ")}
                </div>
              ))}
            </div>
          ) : (
            <Empty>No cycles detected.</Empty>
          )}
        </Card>
        <Card title="God classes">
          {data.god_classes.length ? (
            <ul className="space-y-1 text-sm">
              {data.god_classes.slice(0, 10).map((g: any) => (
                <li key={`${g.file}-${g.name}`}>
                  <span className="font-semibold">{g.name}</span>{" "}
                  <span className="text-xs text-slate-500">
                    {g.lines} lines · {g.methods} members
                  </span>
                  <div>
                    <FileRef path={g.file} />
                  </div>
                </li>
              ))}
            </ul>
          ) : (
            <Empty>No god classes.</Empty>
          )}
        </Card>
        <Card title="Layer violations">
          {data.layer_violations.length ? (
            <ul className="space-y-1 text-xs">
              {data.layer_violations.slice(0, 12).map((v: any, i: number) => (
                <li key={i} className="font-mono">
                  {v.from} → {v.to}
                </li>
              ))}
            </ul>
          ) : (
            <Empty>No layering violations.</Empty>
          )}
        </Card>
      </div>

      <div className="grid gap-5 lg:grid-cols-2">
        <Card title="Code ownership">
          {data.ownership?.available ? (
            <table className="table">
              <thead>
                <tr>
                  <th>Module</th>
                  <th>Top authors</th>
                  <th>Bus factor</th>
                  <th>Commits</th>
                </tr>
              </thead>
              <tbody>
                {data.ownership.modules.map((m: any) => (
                  <tr key={m.module}>
                    <td className="font-medium">{m.module}</td>
                    <td className="text-xs">{m.top_authors.map((a: any) => `${a.name} (${a.commits})`).join(", ")}</td>
                    <td>
                      <Badge tone={m.bus_factor <= 1 ? "high" : "low"}>{m.bus_factor}</Badge>
                    </td>
                    <td>{m.commits}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : (
            <Empty>Git history not available (uploaded archive).</Empty>
          )}
        </Card>
        <Card title="Hotspots (complexity × churn)">
          {data.hotspots.length ? (
            <table className="table">
              <thead>
                <tr>
                  <th>File</th>
                  <th>Complexity</th>
                  <th>Commits</th>
                </tr>
              </thead>
              <tbody>
                {data.hotspots.map((h: any) => (
                  <tr key={h.file}>
                    <td>
                      <FileRef path={h.file} />
                    </td>
                    <td>{h.complexity}</td>
                    <td>{h.commits}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : (
            <Empty>No hotspots.</Empty>
          )}
        </Card>
      </div>
    </div>
  );
}
