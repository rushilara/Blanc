import type { RevisionSummary } from "./live-api";
import { investigationRoot } from "./revision-history";

export type InvestigationGroup = {
  key: string;
  title: string;
  question: string;
  sessions: { root: RevisionSummary; versions: RevisionSummary[] }[];
  versions: RevisionSummary[];
};

/** Display titles only; original questions and independent sessions stay unchanged. */
export function investigationTitle(question: string): string {
  if (/map.*connections|connections between/i.test(question)) return /discord/i.test(question) ? "Discord connections" : "Connections";
  if (/image.hiding|steganography/i.test(question)) return "Image hiding & software";
  const title = question.split(/[?!]/)[0].replace(/^(?:please |can you |show me )/i, "").trim();
  return title.length > 52 ? `${title.slice(0, 49).trimEnd()}…` : title || "Untitled investigation";
}

export function groupInvestigations(history: RevisionSummary[]): InvestigationGroup[] {
  const groups = new Map<string, InvestigationGroup>();
  for (const root of history.filter(item => !item.parent_analysis_id)) {
    const key = root.question.trim().replace(/\s+/g, " ").toLowerCase();
    let group = groups.get(key);
    if (!group) {
      group = { key, title: investigationTitle(root.question), question: root.question, sessions: [], versions: [] };
      groups.set(key, group);
    }
    group.sessions.push({ root, versions: [] });
  }
  const sessions = new Map([...groups.values()].flatMap(group => group.sessions.map(session => [session.root.analysis_id, { group, session }] as const)));
  for (const version of history) {
    const rootId = investigationRoot(history, version.analysis_id);
    const entry = rootId ? sessions.get(rootId) : undefined;
    if (entry) { entry.group.versions.push(version); entry.session.versions.push(version); }
  }
  for (const group of groups.values()) {
    group.versions.sort((a, b) => Date.parse(b.created_at) - Date.parse(a.created_at));
    for (const session of group.sessions) session.versions.sort((a, b) => Date.parse(a.created_at) - Date.parse(b.created_at));
  }
  return [...groups.values()].sort((a, b) => Date.parse(b.versions[0]?.created_at ?? "") - Date.parse(a.versions[0]?.created_at ?? ""));
}
