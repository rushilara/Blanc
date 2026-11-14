// View contract. Validate live responses against the shared JSON schema upstream.
type Finding = { id: string; evidence_ids: string[]; status: string };
export type AnalysisRevision = {
  analysis_id: string;
  case_id: string;
  question: string;
  answer: string;
  claims: (Finding & { text: string })[];
  graph: {
    nodes: { id: string; label: string; type: string; evidence_ids?: string[] }[];
    edges: (Finding & {
      source: string; target: string; type: string; description?: string;
    })[];
  };
  timeline_events: (Finding & {
    description: string;
    timestamp: string | null;
    timestamp_precision?: string;
    timestamp_kind?: string;
  })[];
  unresolved_questions: string[];
  excluded_record_ids: string[];
  needs_review?: boolean;
  validation_errors?: string[];
};
