"use client";

import { useEffect, useId, useRef, useState } from "react";

let initialized = false;

export function Mermaid({ chart }: { chart: string }) {
  const ref = useRef<HTMLDivElement>(null);
  const [error, setError] = useState<string | null>(null);
  const id = useId().replace(/[^a-zA-Z0-9]/g, "");

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const mermaid = (await import("mermaid")).default;
        if (!initialized) {
          mermaid.initialize({ startOnLoad: false, theme: "neutral", securityLevel: "strict", flowchart: { useMaxWidth: true } });
          initialized = true;
        }
        const { svg } = await mermaid.render(`m${id}${Date.now()}`, chart);
        if (!cancelled && ref.current) {
          // mermaid sanitises output when securityLevel is "strict"
          ref.current.innerHTML = svg;
          setError(null);
        }
      } catch (e) {
        if (!cancelled) setError((e as Error).message);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [chart, id]);

  if (error) {
    return <pre className="overflow-auto rounded bg-slate-50 p-3 text-xs text-slate-600">{chart}</pre>;
  }
  return <div ref={ref} className="mermaid-container flex min-h-[120px] justify-center overflow-auto" />;
}
