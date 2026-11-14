// View contract. Validate live responses against the shared JSON schema upstream.
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
  analysis_id: string;
  case_id: string;
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
};
