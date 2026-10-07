export interface StepView {
  key: string;
  status: string;
  description: string;
  action_type: string;
  irreversible: boolean;
  reasons: string[];
  approver: string | null;
  attempts: number;
  retryable: boolean;
}

export interface RunView {
  run_id: string;
  workflow: string;
  tenant_id: string;
  subject_id: string;
  status: string;
  steps: StepView[];
  pending_approvals: string[];
  context: Record<string, unknown>;
}

export interface WorkflowStepView {
  key: string;
  action_type: string;
  description: string;
  requires: string[];
  requires_context: string[];
  irreversible: boolean;
  optional: boolean;
}

export interface WorkflowView {
  name: string;
  steps: WorkflowStepView[];
  required_context: string[];
}

export type WorkflowCatalogue = Record<string, WorkflowView>;

export interface TokenResponse {
  token: string;
  tenant_id: string;
  subject: string;
  roles: string[];
}

export interface LedgerEntryView {
  sequence: number;
  workflow: string;
  step: string;
  action_type: string;
  subject_id: string;
  outcome: string;
  reasons: string[];
  approver: string | null;
  recorded_at: string;
}

export interface ScreeningView {
  subject_key: string;
  score: number;
  recommendation: string;
  rationale: string;
  signals_considered: string[];
  model: string;
  prompt_fingerprint: string;
}

export interface AnonymizeResponse {
  subject_key: string;
  attributes: Record<string, unknown>;
  dropped: string[];
  pseudonymised: string[];
  generalised: string[];
  scrubbed_free_text: string[];
}

export interface GroupImpactView {
  group: string;
  selection_rate: number;
  impact_ratio: number;
  total: number;
  selected: number;
  adversely_impacted: boolean;
}

export interface AdverseImpactResponse {
  report_id?: number | null;
  label?: string | null;
  ledger_sequence?: number | null;
  recorded_at?: string | null;
  minimum_group_size?: number | null;
  verdict: string;
  reference_group: string;
  reference_rate: number;
  groups: GroupImpactView[];
  p_value: number | null;
  summary: string;
}

export interface ModelStatusView {
  trained: boolean;
  algorithm: string | null;
  rows: number | null;
  positives: number | null;
  trained_at: string | null;
  feature_importance: Record<string, number>;
  version?: number | null;
  gate?: string | null;
  data_hash?: string | null;
  created_by?: string | null;
  findings?: string[];
}

export interface ModelVersionView {
  version: number;
  algorithm: string;
  rows: number;
  positives: number;
  data_hash: string;
  gate: string;
  active: boolean;
  created_by: string;
  created_at: string;
  activated_at: string | null;
  fidelity: {
    score?: number | null;
    findings?: string[];
    notes?: string[];
    drift?: { feature: string; severity: string; statistic: number }[];
    determinism?: { case: string; stability: string }[];
  };
}

export interface DriverView {
  feature: string;
  contribution: number;
  direction: string;
}

export interface AttritionScoreView {
  subject_key: string;
  probability: number;
  band: string;
  needs_intervention: boolean;
  drivers: DriverView[];
}

export interface TrainResponse {
  rows: number;
  positives: number;
  positive_rate: number;
  algorithm: string;
  feature_importance: Record<string, number>;
  version?: number;
  gate?: string;
  active?: boolean;
  findings?: string[];
}

export interface IntegrityView {
  signed?: number;
  unsigned?: number;
  signatures_checked?: boolean;
  intact: boolean;
  entries_checked: number;
  broken_at: number | null;
  reason: string | null;
}

export interface Problem {
  title: string;
  detail: string;
  status: number;
  code: string;
  reasons?: string[];
}

export interface OverviewView {
  runs: number;
  awaiting_approval: number;
  awaiting_external: number;
  failed: number;
  ledger_entries: number;
  human_decisions: number;
  screenings: Record<string, number>;
  impact_reports: Record<string, number>;
  retention_bands: Record<string, number>;
  model_trained: boolean;
}

export interface StoredScreeningView {
  id: number;
  subject_key: string;
  requirement: string;
  score: number;
  recommendation: string;
  rationale: string;
  signals_considered: string[];
  model: string;
  prompt_fingerprint: string;
  screened_at: string;
}

export interface StoredScoreView {
  subject_key: string;
  probability: number;
  band: string;
  needs_intervention: boolean;
  drivers: DriverView[];
  scored_at: string;
}


export interface VerificationReport {
  id: number;
  kind: "determinism" | "drift" | "fidelity";
  label: string;
  verdict: string;
  report: Record<string, unknown>;
  ledger_sequence: number | null;
  created_by: string;
  created_at: string;
}

export interface SkillHolding {
  skill: string;
  proficiency: string;
  mentions: number;
  years: number;
}

export interface GapView {
  skill: string;
  required: string;
  supply: number;
  demand: number;
  shortfall: number;
  covered: boolean;
}

export interface GapForecastView {
  horizon_months: number;
  summary: string;
  total_shortfall: number;
  gaps: GapView[];
}

export interface MobilityMatchView {
  subject_key: string;
  role_id: string;
  title: string;
  score: number;
  ready_now: boolean;
  stretch: boolean;
  development_path: string[];
}

export interface WorkforceMonth {
  month: number;
  headcount: number;
  effective_capacity: number;
  leavers: number;
  joiners: number;
  demand: number;
  shortfall: number;
}

export interface WorkforceResult {
  months: number;
  results: {
    scenario: string;
    final_headcount: number;
    total_leavers: number;
    total_hires: number;
    first_shortfall_month: number | null;
    peak_shortfall: number;
    summary: string;
    timeline: WorkforceMonth[];
  }[];
  hires_required: Record<string, number | string>;
}

export interface SentimentGroup {
  group: string;
  respondents: number;
  overall: number;
  aspects: { aspect: string; score: number; mentions: number; negative: boolean }[];
}

export interface SentimentView {
  minimum_group_size: number;
  summary: string;
  groups: SentimentGroup[];
  suppressed_groups: string[];
}

export interface EarlyWarningView {
  warnings: { group: string; aspect: string; previous: number; current: number; drop: number }[];
  ledger_sequence: number;
  previous: SentimentView;
  current: SentimentView;
}

export interface LedgerHeadView {
  sequence: number | null;
  entry_hash: string;
  entries: number;
  signed: boolean;
  signature: string | null;
  key_fingerprint: string | null;
  generated_at: string;
}
