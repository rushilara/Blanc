/** What the investigator clicked: a claim, node, edge or event and the records it cites. */
export type EvidenceSelection = {
  kind: "claim" | "node" | "edge" | "event";
  id: string;
  label: string;
  evidenceIds: string[];
};

export type OnSelectEvidence = (selection: EvidenceSelection) => void;
