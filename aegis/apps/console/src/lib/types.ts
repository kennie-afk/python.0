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
}

export interface IntegrityView {
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
}
