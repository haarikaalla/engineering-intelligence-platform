export interface Scores {
  health: number;
  architecture: number;
  security: number;
  maintainability: number;
  test: number;
}

export interface RepoSummary {
  id: string;
  name: string;
  url: string | null;
  branch: string | null;
  source: string;
  status: "queued" | "running" | "ready" | "failed";
  stage: string;
  progress: number;
  error: string | null;
  commit_sha: string | null;
  created_at: string | null;
  updated_at: string | null;
  scores: Scores | null;
  languages: string[];
  running?: boolean;
}

export interface GraphNode {
  id: string;
  label: string;
  name: string;
  [key: string]: unknown;
}

export interface GraphEdge {
  source: string;
  target: string;
  type: string;
  weight?: number;
  [key: string]: unknown;
}

export interface GraphData {
  nodes: GraphNode[];
  edges: GraphEdge[];
}

export interface Finding {
  rule_id: string;
  title: string;
  category: string;
  severity: string;
  cwe: string;
  file: string;
  line: number;
  snippet: string;
  recommendation: string;
  confidence: string;
}

export interface ChatSource {
  path: string;
  start_line: number;
  end_line: number;
  symbol?: string | null;
}

export interface ChatMessage {
  id?: string;
  role: "user" | "assistant";
  content: string;
  sources?: ChatSource[] | null;
  mode?: string;
  intent?: string;
}

export interface ImpactReport {
  changed: string[];
  risk: { score: number; level: string };
  summary: Record<string, number>;
  services: { name: string; reason: string; depth: number }[];
  apis: { name: string; module: string; file: string; handler: string | null; impact: string }[];
  databases: { name: string; type: string; impact: string; via: string }[];
  tests: { file: string; covers: string; relation: string }[];
  untested_changed_files: string[];
  files: { file: string; depth: number; module: string; is_test: boolean; chain: string[] }[];
  dependency_chains: { file: string; chain: string[] }[];
  graph: GraphData;
}
