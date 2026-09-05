export interface Overview {
  built_at: string;
  users: number;
  items: number;
  interactions: number;
  index_size: number;
  embedding_dimension: number;
  ranker_auc: number;
  ranker_calibrated: boolean;
  tower_final_loss: number;
  live_model: string | null;
  live_stage: string | null;
  served: number;
  guard_healthy: boolean;
  guard_reasons: string[];
  experiment: {
    key: string;
    decision: string;
    likelihood_ratio: number;
    threshold: number;
    control_rate: number;
    treatment_rate: number;
    lift: number;
    samples: number;
  };
}

export interface UserSummary {
  user_id: string;
  topic: string;
  clicks: number;
}

export interface FeedItem {
  item_id: string;
  score: number;
  retrieval_score: number;
  topic: string;
  author: string;
  on_topic: boolean;
  source: string;
  reasons: string[];
  published_at: string;
}

export interface Feed {
  request_id: string;
  user_id: string;
  user_topic: string;
  variant: string;
  retrieved: number;
  latency_ms: number;
  ndcg_at_10: number;
  recall_at_15: number;
  diagnostics: Record<string, unknown>;
  items: FeedItem[];
}

export interface RetrievalResult {
  item_id: string;
  similarity: number;
  topic: string;
  on_topic: boolean;
  in_exact_set: boolean;
}

export interface Retrieval {
  user_id: string;
  user_topic: string;
  k: number;
  recall_vs_brute_force: number;
  approximate_ms: number;
  exact_ms: number;
  speedup: number;
  results: RetrievalResult[];
}

export interface ModelReport {
  rows: number;
  positives: number;
  holdout_auc: number;
  calibrated: boolean;
  features: { name: string; importance: number }[];
  tower: {
    dimension: number;
    first_loss: number;
    final_loss: number;
    users: number;
    items: number;
  };
}

export interface RegistryEntry {
  label: string;
  version: number;
  stage: string;
  traffic: number;
  metrics: Record<string, number>;
  created_at: string;
  history: { at: string; stage: string; reason: string }[];
}

export interface DriftRow {
  feature: string;
  psi: number;
  ks_statistic: number;
  p_value: number;
  severity: string;
  drifted: boolean;
}

export interface ExperimentState {
  key: string;
  variants: { name: string; weight: number }[];
  holdout: number;
  decision: string;
  likelihood_ratio: number;
  threshold: number;
  control: { trials: number; successes: number; rate: number };
  treatment: { trials: number; successes: number; rate: number };
  lift: number;
  samples: number;
}

export interface SimulationResult {
  requests: number;
  wall_ms: number;
  throughput_per_second: number;
  latency_p50_ms: number;
  latency_p95_ms: number;
  latency_p99_ms: number;
  experiment: string;
}
