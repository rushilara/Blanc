import Ajv2020 from "ajv/dist/2020";
import addFormats from "ajv-formats";
import revisionSchema from "../../../contracts/analysis_revision.schema.json";
import recordSchema from "../../../contracts/evidence_record.schema.json";
import { API_BASE_URL } from "./api";
import type { AnalysisRevision, EvidenceRecord } from "./analysis";
import { citedRecordIds } from "./citations";

const ajv = new Ajv2020({ allErrors: true });
addFormats(ajv);
const validRevision = ajv.compile<AnalysisRevision>(revisionSchema);
const validRecord = ajv.compile<EvidenceRecord>(recordSchema);

export class ApiError extends Error {
  constructor(message: string, public status?: number) { super(message); }
}

export type CaseSummary = { case_id: string; title: string; import_status: string };
export type SearchRecord = {
  record_id: string; source_file: string; source_locator: string;
  excerpts: { text: string; char_start: number; char_end: number; truncated?: boolean }[];
};
export type SearchResult = {
  case_id: string; question: string; records: SearchRecord[];
  dropped_by_limit: number; dropped_by_budget: number;
};
const object = (v: unknown): v is Record<string, unknown> => v !== null && typeof v === "object" && !Array.isArray(v);
const text = (v: unknown): v is string => typeof v === "string";
const nonnegative = (v: unknown): v is number => typeof v === "number" && Number.isSafeInteger(v) && v >= 0;
const path = (caseId: string) => `/cases/${encodeURIComponent(caseId)}`;

async function request(url: string, signal?: AbortSignal, body?: unknown): Promise<unknown> {
  const response = await fetch(`${API_BASE_URL}${url}`, {
    method: body === undefined ? "GET" : "POST", cache: "no-store", signal,
    ...(body === undefined ? {} : { headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }),
  });
  if (!response.ok) {
    // Do not display raw provider errors, which may contain credentials or internals.
    throw new ApiError(response.status === 503 ? "The analysis service is not ready. Please try again later." : `The request failed (HTTP ${response.status}).`, response.status);
  }
  try { return await response.json(); }
  catch { throw new ApiError("The backend returned invalid JSON."); }
}

export async function listCases(signal?: AbortSignal): Promise<CaseSummary[]> {
  const data = await request("/cases", signal);
  if (!Array.isArray(data) || !data.every(v => object(v) && text(v.case_id) && text(v.title) && text(v.import_status))) {
    throw new ApiError("The case list response is invalid.");
  }
  return data as CaseSummary[];
}

export async function searchCase(caseId: string, question: string, signal?: AbortSignal): Promise<SearchResult> {
  const data = await request(`${path(caseId)}/search`, signal, { question });
  if (!object(data) || data.case_id !== caseId || data.question !== question || !Array.isArray(data.records)
    || !nonnegative(data.dropped_by_limit) || !nonnegative(data.dropped_by_budget)
    || !data.records.every(r => object(r) && text(r.record_id) && text(r.source_file) && text(r.source_locator)
      && Array.isArray(r.excerpts) && r.excerpts.length > 0 && r.excerpts.every(e => object(e)
        && text(e.text) && nonnegative(e.char_start) && nonnegative(e.char_end) && e.char_end > e.char_start))) {
    throw new ApiError("The search response is invalid or belongs to a different case/question.");
  }
  return data as SearchResult;
}

export async function getSource(caseId: string, recordId: string, signal?: AbortSignal): Promise<EvidenceRecord> {
  const data = await request(`${path(caseId)}/records/${encodeURIComponent(recordId)}`, signal);
  if (!object(data) || !validRecord(data.record) || data.record.case_id !== caseId || data.record.record_id !== recordId) {
    throw new ApiError("The source response is invalid or belongs to a different case/record.");
  }
  return data.record;
}

export function parseRevision(data: unknown, caseId: string, question: string): AnalysisRevision {
  if (!validRevision(data) || data.case_id !== caseId || data.question !== question) {
    throw new ApiError("The analysis response does not match the shared contract or selected case/question.");
  }
  const permitted = new Set(data.retrieved_record_ids.filter(id => !data.excluded_record_ids.includes(id)));
  const nodes = new Set(data.graph.nodes.map(node => node.id));
  const findings = [...data.claims, ...data.graph.edges, ...data.timeline_events];
  if (nodes.size !== data.graph.nodes.length
    || findings.some(f => !f.evidence_ids.every(id => permitted.has(id)))
    || data.graph.nodes.some(node => node.evidence_ids?.some(id => !permitted.has(id)))
    || data.graph.edges.some(edge => !nodes.has(edge.source) || !nodes.has(edge.target))) {
    throw new ApiError("The analysis contains invalid or excluded source references.");
  }
  return data;
}

export async function analyzeCase(caseId: string, question: string, signal?: AbortSignal): Promise<AnalysisRevision> {
  try {
    return parseRevision(await request(`${path(caseId)}/analyses`, signal, { question }), caseId, question);
  } catch (error) {
    if (error instanceof ApiError && [404, 405, 501].includes(error.status ?? 0)) {
      throw new ApiError("Answer generation is not available on this backend yet. You can search and inspect the evidence now.", error.status);
    }
    throw error;
  }
}

export type RevisionSummary = {
  analysis_id: string;
  parent_analysis_id: string | null;
  question: string;
  needs_review: boolean;
  created_at: string;
  excluded_record_ids: string[];
  restored_from_analysis_id: string | null;
};
const id = (value: unknown): value is string => text(value) && value.length > 0;
const nullableId = (value: unknown) => value === null || id(value);

export async function listRevisions(caseId: string, signal?: AbortSignal): Promise<RevisionSummary[]> {
  const data = await request(`${path(caseId)}/analyses`, signal);
  if (!Array.isArray(data) || !data.every(r => object(r) && id(r.analysis_id)
    && nullableId(r.parent_analysis_id) && text(r.question) && typeof r.needs_review === "boolean"
    && text(r.created_at) && /^\d{4}-\d{2}-\d{2}T.*(?:Z|[+-]\d{2}:\d{2})$/.test(r.created_at)
    && Number.isFinite(Date.parse(r.created_at)) && nullableId(r.restored_from_analysis_id)
    && Array.isArray(r.excluded_record_ids) && r.excluded_record_ids.every(id)
    && new Set(r.excluded_record_ids).size === r.excluded_record_ids.length)) {
    throw new ApiError("The saved version history is invalid.");
  }
  const revisions = data as RevisionSummary[];
  const byId = new Map(revisions.map(r => [r.analysis_id, r]));
  if (byId.size !== revisions.length) throw new ApiError("The saved version history has duplicate IDs.");
  for (const revision of revisions) {
    const seen = new Set<string>();
    let current: RevisionSummary | undefined = revision;
    while (current) {
      if (seen.has(current.analysis_id)) throw new ApiError("The saved version history has a cycle.");
      seen.add(current.analysis_id);
      if (current.parent_analysis_id === null) break;
      current = byId.get(current.parent_analysis_id);
      if (!current) throw new ApiError("The saved version history has a missing parent.");
    }
  }
  return revisions;
}

export async function getRevision(caseId: string, analysisId: string, signal?: AbortSignal): Promise<AnalysisRevision> {
  const data = await request(`${path(caseId)}/analyses/${encodeURIComponent(analysisId)}`, signal);
  if (!object(data) || data.analysis_id !== analysisId || !text(data.question)) {
    throw new ApiError("The saved version does not match the requested revision.");
  }
  return parseRevision(data, caseId, data.question);
}

export async function loadRevisionSources(revision: AnalysisRevision, signal?: AbortSignal): Promise<EvidenceRecord[]> {
  // Every cited original (claims, nodes, edges, events) plus excluded ones for the excluded list.
  const ids = [...new Set([...citedRecordIds(revision), ...revision.excluded_record_ids])];
  const responses = await Promise.allSettled(ids.map(recordId => getSource(revision.case_id, recordId, signal)));
  return responses.flatMap(r => r.status === "fulfilled" ? [r.value] : []);
}
