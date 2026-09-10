export interface TaskData {
  task_id: string;
  agent_name: string;
  status: string;
  response?: string;
  instruction?: string;
  depends_on?: string[];
  latency_ms?: number;
}
export interface Evidence {
  document_id?: string;
  title?: string;
  product_model?: string;
  section?: string;
  citation?: string;
  excerpt?: string;
  rerank_score?: number;
  reranker_mode?: string;
}
export interface TraceEvent {
  type: string;
  sequence: number;
  timestamp: string;
  data: {
    task_id?: string;
    agent_name?: string;
    status?: string;
    response?: string;
    instruction?: string;
    depends_on?: string[];
    latency_ms?: number;
    results?: Evidence[];
    query?: string;
    stage?: string;
    label?: string;
    content?: string;
    error?: string;
    cached?: boolean;
  };
}
export interface Run {
  id: string;
  conversation_id: string;
  query: string;
  response: string;
  status: "running" | "completed" | "failed" | "cancelled";
  events: TraceEvent[];
  created_at: string;
  error?: string;
}
