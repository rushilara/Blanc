// View contract. Validate live responses against the shared JSON schema upstream.
export type EvidenceRecord = {
  record_id: string;
  case_id: string;
  source_file: string;
  source_locator: string;
  content: string;
  timestamp?: string | null;
  timestamp_kind?: string;
};
type SupportedStatus = "observed" | "reported" | "inferred";
type ClaimStatus = SupportedStatus | "unresolved";
type NodeType = "person" | "account" | "device" | "location" | "organization" | "document" | "other";
type TimestampPrecision = "exact" | "day" | "month" | "approximate" | "unknown";
type TimestampKind = "observed" | "source_stated" | "inferred" | "unknown";
type Finding<Status extends ClaimStatus = SupportedStatus> = {
  id: string;
  evidence_ids: string[];
  status: Status;
};
export type AnalysisRevision = {
  schema_version: "1.0";
  analysis_id: string;
  parent_analysis_id?: string | null;
  case_id: string;
  retrieved_record_ids: string[];
  created_at: string;
  question: string;
  answer: string;
  claims: (Finding<ClaimStatus> & { text: string })[];
  graph: {
    nodes: { id: string; label: string; type: NodeType; evidence_ids?: string[] }[];
    edges: (Finding & {
      source: string; target: string; type: string; description?: string;
    })[];
  };
  timeline_events: (Finding & {
    description: string;
    timestamp: string | null;
    timestamp_precision?: TimestampPrecision;
    timestamp_kind?: TimestampKind;
  })[];
  unresolved_questions: string[];
  excluded_record_ids: string[];
  needs_review?: boolean;
  validation_errors?: string[];
  retrieval_coverage?: RetrievalCoverage;
};

// Server-assigned record of what the evidence window left out (BLA-64). Absent on older
// revisions: treat those as the standard window with no offer.
export type EvidenceWindow = "standard" | "expanded";
export type RetrievalCoverage = {
  evidence_window: EvidenceWindow;
  max_records: number;
  max_excerpt_chars: number;
  // IDs and ranks only, never content, and never citations.
  skipped: { record_id: string; rank: number; reason: "budget" | "record_limit"; vector_rank?: number; keyword_rank?: number }[];
  budget_substituted: boolean;
  // Records one search ranked inside the window but fusion pushed out: "N relevant records were left out".
  left_out_record_ids: string[];
  // Standard window and something was left out: show "Want more evidence?".
  offer_expanded_window: boolean;
};
