import assert from "node:assert/strict";
import { test } from "node:test";
import type { EvidenceRecord } from "../src/lib/analysis";
import { readNodeConnections } from "../src/lib/connection-reading";

const history = (id: string, url: string, title: string, visits = 1): EvidenceRecord => ({
  record_id: id,
  case_id: "case",
  source_file: "Users/Steve/Google/Chrome/User Data/Default/History",
  source_locator: `SQLite urls.id=${id}`,
  content: JSON.stringify({ url, title, visit_count: visits }),
  timestamp: null,
});

const name = (id: string) => ({ steve: "Steve", stokes: "Stokes Valley, Lower Hutt", east: "Eastbourne" }[id] ?? id);

test("a clicked person is explained from the cited records, not the relationship label", () => {
  const place = history("r1", "https://www.google.com/maps/place/Stokes+Valley,+Lower+Hutt+5019/@-41.1,174.9,14z", "Google Maps", 4);
  const same = history("r2", "https://www.google.com/maps/place/Stokes+Valley,+Lower+Hutt+5019/@-41.1,174.9,14z", "Google Maps", 4);
  const directions = history("r3", "https://www.google.com/maps/dir//Eastbourne/@-41.2,174.9,13z", "Google Maps");
  const reading = readNodeConnections({
    nodeId: "steve",
    label: "Steve",
    name,
    edges: [
      { id: "e1", source: "steve", target: "stokes", type: "searched_location", description: "searched location", status: "observed", evidence_ids: ["r1", "r2"] },
      { id: "e2", source: "steve", target: "east", type: "searched_location", description: "Steve opened directions to Eastbourne.", status: "observed", evidence_ids: ["r3"] },
    ],
    records: [place, same, directions],
    claims: [],
    events: [],
    questions: [],
  });
  assert.equal(reading.connections[0].stated, undefined);
  assert.equal(reading.connections[0].otherLabel, "Stokes Valley, Lower Hutt");
  assert.deepEqual(reading.connections[0].facts, [{
    line: "Google Maps place page for Stokes Valley, Lower Hutt 5019. Recorded visits: 4.",
    evidenceIds: ["r1", "r2"],
  }]);
  assert.equal(reading.connections[1].stated, "Steve opened directions to Eastbourne.");
  assert.match(reading.connections[1].facts[0].line, /Directions to Eastbourne/);
});

test("records and claims that mention the person but are not an edge are listed as not drawn", () => {
  const maps = history("r1", "https://www.google.com/maps/place/Eastbourne/@-41.2,174.9,13z", "Google Maps");
  const search = history("r9", "https://www.google.com/search?q=courtenay+place", "courtenay place - Google Search");
  const reading = readNodeConnections({
    nodeId: "steve",
    label: "Steve",
    name,
    edges: [
      { id: "e1", source: "steve", target: "east", type: "searched_location", status: "observed", evidence_ids: ["r1"] },
    ],
    records: [maps, search],
    claims: [
      { id: "c1", text: "Steve searched Eastbourne on Google Maps.", status: "observed", evidence_ids: ["r1"] },
      { id: "c2", text: "Steve also searched for courtenay place on the web.", status: "observed", evidence_ids: ["r9"] },
    ],
    events: [],
    questions: ["Where else did Steve go after Eastbourne?"],
  });
  assert.equal(reading.connections.length, 1);
  assert.deepEqual(reading.missed.map(item => item.id), ["c2", "question-0"]);
  assert.match(reading.missed[0].text, /courtenay place/);
  assert.deepEqual(reading.missed[0].evidenceIds, ["r9"]);
});
