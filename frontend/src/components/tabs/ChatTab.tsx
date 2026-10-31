"use client";

import { useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { Bot, Loader2, Send, Trash2, User } from "lucide-react";
import { api, post } from "@/lib/api";
import type { ChatMessage } from "@/lib/types";
import { Badge, Card, ErrorBox } from "@/components/ui";

const SUGGESTIONS = [
  "Explain this repository",
  "What problem does it solve and how can we use it?",
  "What should we develop next? Any suggestions?",
  "Is it enterprise-ready?",
  "Where is authentication implemented?",
  "Which services depend on PaymentService?",
  "What happens if I change OrderController?",
  "Which modules interact with the database?",
  "Describe the main data flows",
  "What are the biggest security risks?",
  "Which risky code has no tests?",
];

export function ChatTab({ repoId }: { repoId: string }) {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const bottom = useRef<HTMLDivElement>(null);

  useEffect(() => {
    api<ChatMessage[]>(`/repositories/${repoId}/chat`).then(setMessages).catch((e) => setError(e.message));
  }, [repoId]);

  useEffect(() => bottom.current?.scrollIntoView({ behavior: "smooth" }), [messages, busy]);

  async function send(text: string) {
    const message = text.trim();
    if (!message || busy) return;
    setInput("");
    setBusy(true);
    setError(null);
    setMessages((m) => [...m, { role: "user", content: message }]);
    try {
      const res = await post<{ answer: string; sources: ChatMessage["sources"]; intent: string; mode: string }>(`/repositories/${repoId}/chat`, { message });
      setMessages((m) => [...m, { role: "assistant", content: res.answer, sources: res.sources, intent: res.intent, mode: res.mode }]);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function clear() {
    await api(`/repositories/${repoId}/chat`, { method: "DELETE" });
    setMessages([]);
  }

  return (
    <Card
      title="Repository intelligence chat"
      actions={
        <button className="btn-secondary py-1 text-xs" onClick={() => void clear()} disabled={!messages.length}>
          <Trash2 className="h-3.5 w-3.5" /> Clear
        </button>
      }
    >
      <div className="flex h-[640px] flex-col">
        <div className="flex-1 space-y-4 overflow-y-auto pr-2">
          {!messages.length && (
            <div className="py-10 text-center text-sm text-slate-500">
              Ask anything about the codebase. Answers are grounded in the knowledge graph and retrieved source code.
            </div>
          )}
          {messages.map((m, i) => (
            <div key={i} className={`flex gap-3 ${m.role === "user" ? "justify-end" : ""}`}>
              {m.role === "assistant" && (
                <div className="mt-1 h-8 w-8 shrink-0 rounded-full bg-brand-600 p-1.5 text-white">
                  <Bot className="h-5 w-5" />
                </div>
              )}
              <div className={`max-w-[85%] rounded-xl px-4 py-3 text-sm ${m.role === "user" ? "bg-brand-600 text-white" : "border border-slate-200 bg-white"}`}>
                {m.role === "user" ? (
                  m.content
                ) : (
                  <>
                    <div className="markdown">
                      <ReactMarkdown remarkPlugins={[remarkGfm]}>{m.content}</ReactMarkdown>
                    </div>
                    {!!m.sources?.length && (
                      <div className="mt-3 border-t border-slate-100 pt-2">
                        <div className="mb-1 text-[11px] font-semibold uppercase text-slate-400">Sources</div>
                        <div className="flex flex-wrap gap-1">
                          {m.sources.map((s) => (
                            <span key={`${s.path}:${s.start_line}`} className="rounded bg-slate-100 px-1.5 py-0.5 font-mono text-[11px] text-slate-600">
                              {s.path}:{s.start_line}-{s.end_line}
                            </span>
                          ))}
                        </div>
                      </div>
                    )}
                    {(m.intent || m.mode) && (
                      <div className="mt-2 flex gap-1">
                        {m.intent && <Badge>{m.intent}</Badge>}
                        {m.mode && <Badge>{m.mode}</Badge>}
                      </div>
                    )}
                  </>
                )}
              </div>
              {m.role === "user" && (
                <div className="mt-1 h-8 w-8 shrink-0 rounded-full bg-slate-200 p-1.5 text-slate-600">
                  <User className="h-5 w-5" />
                </div>
              )}
            </div>
          ))}
          {busy && (
            <div className="flex items-center gap-2 text-sm text-slate-500">
              <Loader2 className="h-4 w-4 animate-spin" /> Reasoning over the knowledge graph and code…
            </div>
          )}
          <div ref={bottom} />
        </div>
        {error && <ErrorBox message={error} />}
        <div className="mt-3 flex flex-wrap gap-1.5">
          {SUGGESTIONS.map((s) => (
            <button key={s} className="rounded-full border border-slate-200 px-3 py-1 text-xs text-slate-600 hover:border-brand-300 hover:bg-brand-50" onClick={() => void send(s)} disabled={busy}>
              {s}
            </button>
          ))}
        </div>
        <form
          className="mt-3 flex gap-2"
          onSubmit={(e) => {
            e.preventDefault();
            void send(input);
          }}
        >
          <input className="input" placeholder="Ask about architecture, dependencies, impact, security…" value={input} onChange={(e) => setInput(e.target.value)} maxLength={4000} />
          <button className="btn-primary" disabled={busy || !input.trim()}>
            <Send className="h-4 w-4" /> Ask
          </button>
        </form>
      </div>
    </Card>
  );
}
