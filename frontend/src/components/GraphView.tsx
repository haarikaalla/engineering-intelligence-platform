"use client";

import { useEffect, useRef } from "react";
import type { GraphData, GraphNode } from "@/lib/types";

export const NODE_COLORS: Record<string, string> = {
  Repository: "#0f172a",
  Service: "#4f46e5",
  Module: "#6366f1",
  File: "#64748b",
  Class: "#0891b2",
  Function: "#10b981",
  API: "#f59e0b",
  Database: "#dc2626",
  Table: "#f87171",
  Topic: "#a855f7",
  Infrastructure: "#78716c",
  Changed: "#dc2626",
  Test: "#22c55e",
};

const SHAPES: Record<string, string> = {
  Repository: "star",
  Service: "round-hexagon",
  Module: "round-rectangle",
  Database: "barrel",
  Table: "barrel",
  API: "round-tag",
  Topic: "diamond",
  Infrastructure: "octagon",
  Changed: "round-rectangle",
};

interface Props {
  data: GraphData;
  layout?: "cose" | "breadthfirst" | "concentric" | "circle";
  height?: number;
  onNodeClick?: (node: GraphNode) => void;
  showEdgeLabels?: boolean;
}

export function GraphView({ data, layout = "cose", height = 560, onNodeClick, showEdgeLabels = false }: Props) {
  const container = useRef<HTMLDivElement>(null);
  const clickRef = useRef(onNodeClick);
  clickRef.current = onNodeClick;

  useEffect(() => {
    let cy: import("cytoscape").Core | null = null;
    let disposed = false;
    (async () => {
      const cytoscape = (await import("cytoscape")).default;
      if (disposed || !container.current) return;
      const ids = new Set(data.nodes.map((n) => n.id));
      const elements = [
        ...data.nodes.map((n) => ({ data: { ...n, id: n.id, display: String(n.name).slice(0, 40), type: n.label } })),
        ...data.edges
          .filter((e) => ids.has(e.source) && ids.has(e.target))
          .map((e, i) => ({ data: { id: `e${i}`, source: e.source, target: e.target, type: e.type, weight: e.weight ?? 1 } })),
      ];
      cy = cytoscape({
        container: container.current,
        elements,
        wheelSensitivity: 0.25,
        style: [
          {
            selector: "node",
            style: {
              label: "data(display)",
              "font-size": 9,
              color: "#1e293b",
              "text-valign": "bottom",
              "text-margin-y": 3,
              "background-color": (ele: any) => NODE_COLORS[ele.data("type")] ?? "#94a3b8",
              shape: (ele: any) => (SHAPES[ele.data("type")] ?? "ellipse") as any,
              width: (ele: any) => (["Repository", "Service", "Database"].includes(ele.data("type")) ? 30 : 18),
              height: (ele: any) => (["Repository", "Service", "Database"].includes(ele.data("type")) ? 30 : 18),
              "border-width": 1,
              "border-color": "#ffffff",
            },
          },
          {
            selector: "edge",
            style: {
              width: (ele: any) => Math.min(6, 1 + Math.log2(Number(ele.data("weight")) || 1)),
              "line-color": "#cbd5e1",
              "target-arrow-color": "#94a3b8",
              "target-arrow-shape": "triangle",
              "curve-style": "bezier",
              "arrow-scale": 0.8,
              label: showEdgeLabels ? "data(type)" : "",
              "font-size": 7,
              color: "#64748b",
              "text-rotation": "autorotate",
            },
          },
          { selector: 'edge[type = "DEPENDS_ON"]', style: { "line-color": "#818cf8", "target-arrow-color": "#6366f1" } },
          { selector: 'edge[type = "ACCESSES"]', style: { "line-color": "#fca5a5", "target-arrow-color": "#ef4444", "line-style": "dashed" } },
          { selector: 'edge[type = "PUBLISHES"], edge[type = "SUBSCRIBES"]', style: { "line-color": "#d8b4fe", "line-style": "dotted" } },
          { selector: "node:selected", style: { "border-width": 3, "border-color": "#f59e0b" } },
        ],
        layout: {
          name: layout,
          animate: false,
          padding: 20,
          ...(layout === "cose" ? { nodeRepulsion: () => 9000, idealEdgeLength: () => 80 } : {}),
          ...(layout === "breadthfirst" ? { directed: true, spacingFactor: 1.1 } : {}),
        } as any,
      });
      cy.on("tap", "node", (evt) => clickRef.current?.(evt.target.data() as GraphNode));
    })();
    return () => {
      disposed = true;
      cy?.destroy();
    };
  }, [data, layout, showEdgeLabels]);

  return <div ref={container} style={{ height }} className="w-full rounded-lg border border-slate-200 bg-slate-50" />;
}

export function GraphLegend({ labels }: { labels: string[] }) {
  return (
    <div className="flex flex-wrap gap-3 text-xs text-slate-600">
      {labels.map((l) => (
        <span key={l} className="flex items-center gap-1">
          <span className="inline-block h-3 w-3 rounded-full" style={{ background: NODE_COLORS[l] ?? "#94a3b8" }} /> {l}
        </span>
      ))}
    </div>
  );
}
