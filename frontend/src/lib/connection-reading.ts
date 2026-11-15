import type { EvidenceRecord } from "./analysis";
import { presentSource } from "./source-presentation";

export type ReadingFact = { line: string; evidenceIds: string[] };

export type ConnectionReading = {
  edgeId: string;
  otherLabel: string;
  status: string;
  /** The model's own sentence, when it says more than the relationship label. */
  stated?: string;
  facts: ReadingFact[];
};

export type MissedNote = {
  id: string;
  status?: string;
  text: string;
  evidenceIds: string[];
};

type EdgeLike = {
  id: string;
  source: string;
  target: string;
  type: string;
  description?: string;
  status: string;
  evidence_ids: string[];
};

type Mention = { id: string; text: string; status?: string; evidence_ids: string[] };

const norm = (value: string) => value.toLowerCase().replace(/[_-]+/g, " ").replace(/\s+/g, " ").trim();

function labelOnly(description: string | undefined, type: string) {
  if (!description?.trim()) return true;
  const written = norm(description);
  const kind = norm(type);
  return written === kind || written === `${kind} location` || kind === `${written} location`;
}

function mentions(text: string, label: string) {
  const name = label.trim();
  if (name.length < 3) return false;
  const escaped = name.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  return new RegExp(`(?:^|[^\\p{L}\\p{N}_])${escaped}(?=$|[^\\p{L}\\p{N}_])`, "iu").test(text);
}

const clip = (value: string, max = 180) => {
  const flat = value.replace(/\s+/g, " ").trim();
  return flat.length > max ? `${flat.slice(0, max - 1).trimEnd()}…` : flat;
};

/** One sentence taken from the record's own fields. Nothing is inferred. */
export function recordFact(record: EvidenceRecord): string {
  const view = presentSource(record);
  if (!view) {
    const flat = clip(record.content, 160);
    return flat || "Cited record has no readable text.";
  }
  const field = (label: string) => view.fields.find(item => item.label === label)?.value;
  const visits = field("Recorded visits");
  const visitNote = visits && visits !== "0" ? ` Recorded visits: ${visits}.` : "";
  const place = field("Place");
  if (place) return `Google Maps place page for ${place}.${visitNote}`;
  const directions = field("Directions");
  if (directions) {
    const named = view.name.startsWith("Directions") || view.name.startsWith("Route:")
      ? view.name
      : `Google Maps directions: ${directions}`;
    return `${named}.${visitNote}`;
  }
  const search = field("Map search");
  if (search) return `Google Maps search for “${search}”.${visitNote}`;
  if (view.kind.startsWith("Web search") && view.anchors[0]) return `Web search for “${view.anchors[0]}”.${visitNote}`;
  if (view.message) return `${view.kind}. ${view.name}: “${clip(view.message)}”`;
  if (view.kind.startsWith("Download")) return `Download of ${view.name}.`;
  return `${view.kind}: ${view.name}.${visitNote}`;
}

function recordMentions(record: EvidenceRecord, label: string) {
  const view = presentSource(record);
  const parts = view
    ? [view.name, view.kind, view.message ?? "", ...view.anchors, ...view.fields.map(field => field.value)]
    : [record.content];
  return parts.some(part => mentions(part, label));
}

function groupFacts(ids: string[], records: Map<string, EvidenceRecord>): ReadingFact[] {
  const groups = new Map<string, string[]>();
  for (const id of ids) {
    const record = records.get(id);
    const line = record ? recordFact(record) : "This cited record is not loaded.";
    const bucket = groups.get(line) ?? [];
    if (!bucket.includes(id)) bucket.push(id);
    groups.set(line, bucket);
  }
  return [...groups].map(([line, evidenceIds]) => ({ line, evidenceIds }));
}

export function readNodeConnections(input: {
  nodeId: string;
  label: string;
  name: (id: string) => string;
  edges: EdgeLike[];
  records: EvidenceRecord[];
  claims: Mention[];
  events: Mention[];
  questions: string[];
}): { connections: ConnectionReading[]; missed: MissedNote[]; hiddenSources: number } {
  const records = new Map(input.records.map(record => [record.record_id, record]));
  const touching = input.edges.filter(edge => edge.source === input.nodeId || edge.target === input.nodeId);
  const cited = new Set(touching.flatMap(edge => edge.evidence_ids));
  const connections = touching.map(edge => {
    const otherId = edge.source === input.nodeId ? edge.target : edge.source;
    return {
      edgeId: edge.id,
      otherLabel: input.name(otherId),
      status: edge.status,
      stated: labelOnly(edge.description, edge.type) ? undefined : edge.description!.trim(),
      facts: groupFacts(edge.evidence_ids, records),
    };
  });

  const outside = (ids: string[]) => ids.some(id => !cited.has(id));
  const missed: MissedNote[] = [];
  for (const claim of input.claims) {
    if (mentions(claim.text, input.label) && outside(claim.evidence_ids)) {
      missed.push({ id: claim.id, status: claim.status, text: claim.text, evidenceIds: claim.evidence_ids });
    }
  }
  for (const event of input.events) {
    if (mentions(event.text, input.label) && outside(event.evidence_ids)) {
      missed.push({ id: event.id, status: event.status, text: event.text, evidenceIds: event.evidence_ids });
    }
  }
  input.questions.forEach((question, index) => {
    if (mentions(question, input.label)) missed.push({ id: `question-${index}`, text: question, evidenceIds: [] });
  });

  const sourceNotes: MissedNote[] = [];
  for (const record of input.records) {
    if (cited.has(record.record_id) || !recordMentions(record, input.label)) continue;
    sourceNotes.push({ id: record.record_id, text: recordFact(record), evidenceIds: [record.record_id] });
  }
  const shownSources = sourceNotes.slice(0, 6);
  return { connections, missed: [...missed, ...shownSources], hiddenSources: sourceNotes.length - shownSources.length };
}
