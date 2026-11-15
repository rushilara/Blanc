"use client";

import dynamic from "next/dynamic";
import { useCallback, useEffect, useId, useMemo, useRef, useState } from "react";

import type { AnalysisRevision, EvidenceRecord } from "@/lib/analysis";
import { SourceReferences } from "@/components/SourceReferences";
import { Status } from "@/components/StatusBadge";
import { resolveEdgeSelection } from "@/lib/graphEdges";
import {
  STATUS_EDGE,
  partitionEdges,
  relationLabel,
  sourceCountLabel,
  uniqueIds,
  type GraphEdge,
} from "@/lib/graphElements";
import {
  collectEdgeTypes,
  collectNodeTypes,
  filterGraph,
} from "@/lib/graphFilter";

const RelationshipGraphCanvas = dynamic(() => import("./RelationshipGraphCanvas"), {
  ssr: false,
  loading: () => (
    <p role="status" className="p-5 text-sm text-zinc-500">
      Loading relationship graph…
    </p>
  ),
});

type Graph = AnalysisRevision["graph"];

function TypeFilter({
  label,
  options,
  selected,
  onChange,
}: {
  label: string;
  options: string[];
  selected: string[];
  onChange: (next: string[]) => void;
}) {
  if (!options.length) return null;
  return (
    <fieldset className="space-y-1.5">
      <legend className="text-xs font-medium text-zinc-600 dark:text-zinc-300">{label}</legend>
      <div className="flex flex-wrap gap-1.5">
        {options.map((option) => {
          const on = selected.includes(option);
          return (
            <button
              key={option}
              type="button"
              aria-pressed={on}
              onClick={() =>
                onChange(on ? selected.filter((value) => value !== option) : [...selected, option])
              }
              className={`rounded border px-2 py-0.5 text-xs capitalize ${
                on
                  ? "border-blue-400 bg-blue-50 text-blue-900 dark:border-blue-700 dark:bg-blue-950 dark:text-blue-100"
                  : "border-zinc-300 text-zinc-600 dark:border-zinc-700 dark:text-zinc-300"
              }`}
            >
              {option.replaceAll("_", " ")}
            </button>
          );
        })}
      </div>
    </fieldset>
  );
}

export function RelationshipGraph({
  graph,
  caseId,
  records,
  excludedRecordIds,
  pinnedEntityIds,
  onPinnedEntityIdsChange,
  pinPersistence = "local",
  onExclude,
  onAnnotate,
}: {
  graph: Graph;
  caseId: string;
  records: EvidenceRecord[];
  excludedRecordIds?: string[];
  /** Controlled pin set (board-backed or parent state). */
  pinnedEntityIds?: string[];
  onPinnedEntityIdsChange?: (ids: string[]) => void | Promise<void>;
  /** local: parent may omit handlers; board: pin requires evidence and parent persistence. */
  pinPersistence?: "local" | "board";
  onExclude?: (recordId: string) => void;
  onAnnotate?: (recordId: string) => void;
}) {
  const [drawerIds, setDrawerIds] = useState<string[]>([]);
  const [openToken, setOpenToken] = useState(0);
  const [status, setStatus] = useState<string | null>(null);
  const [pair, setPair] = useState<GraphEdge[] | null>(null);
  const [query, setQuery] = useState("");
  const [nodeTypes, setNodeTypes] = useState<string[]>([]);
  const [edgeTypes, setEdgeTypes] = useState<string[]>([]);
  const [localPins, setLocalPins] = useState<string[]>([]);
  const [pinBusy, setPinBusy] = useState<string | null>(null);
  const pairHeading = useRef<HTMLHeadingElement>(null);
  const pairTitleId = useId();
  const searchId = useId();

  const pinnedIds = pinnedEntityIds ?? localPins;
  const setPinnedIds = useCallback(
    async (next: string[]) => {
      if (onPinnedEntityIdsChange) await onPinnedEntityIdsChange(next);
      else setLocalPins(next);
    },
    [onPinnedEntityIdsChange],
  );

  const nodeTypeOptions = useMemo(() => collectNodeTypes(graph.nodes), [graph.nodes]);
  const edgeTypeOptions = useMemo(() => collectEdgeTypes(graph.edges), [graph.edges]);
  const filtered = useMemo(
    () => filterGraph(graph.nodes, graph.edges, { query, nodeTypes, edgeTypes, pinnedIds }),
    [graph.nodes, graph.edges, query, nodeTypes, edgeTypes, pinnedIds],
  );
  const { drawable, dangling } = useMemo(
    () => partitionEdges(filtered.nodes, filtered.edges),
    [filtered.nodes, filtered.edges],
  );
  const danglingIds = new Set(dangling.map((edge) => edge.id));
  const names = new Map(graph.nodes.map((node) => [node.id, node.label]));
  const name = (id: string) => names.get(id) ?? id;
  const sourceProps = { records, caseId, excludedRecordIds, onExclude, onAnnotate };
  const canvasKey = `${drawable.map((e) => e.id).join(",")}|${filtered.nodes.map((n) => n.id).join(",")}|${pinnedIds.join(",")}`;

  useEffect(() => {
    if (pair) pairHeading.current?.focus();
  }, [pair]);

  const inspect = useCallback((evidenceIds: string[], label: string) => {
    const ids = uniqueIds(evidenceIds);
    if (!ids.length) {
      setStatus(`No cited sources for ${label}.`);
      return;
    }
    setStatus(null);
    setDrawerIds(ids);
    setOpenToken((token) => token + 1);
  }, []);

  const onInspectNode = useCallback(
    (nodeId: string) => {
      const node = graph.nodes.find((item) => item.id === nodeId);
      if (node) inspect(node.evidence_ids ?? [], node.label);
    },
    [graph.nodes, inspect],
  );

  const onSelectEdge = useCallback(
    (edgeId: string) => {
      const selection = resolveEdgeSelection(drawable, edgeId);
      if (selection.kind === "sources") {
        setPair(null);
        inspect(selection.edge.evidence_ids, relationLabel(selection.edge.type));
      } else if (selection.kind === "pair") {
        setStatus(null);
        setPair(selection.edges);
      }
    },
    [drawable, inspect],
  );

  async function togglePin(nodeId: string) {
    const node = graph.nodes.find((item) => item.id === nodeId);
    if (!node) return;
    const pinned = pinnedIds.includes(nodeId);
    if (!pinned && pinPersistence === "board" && !(node.evidence_ids?.length)) {
      setStatus(`Cannot pin ${node.label}: it cites no evidence, so it cannot be saved on the board.`);
      return;
    }
    const next = pinned ? pinnedIds.filter((id) => id !== nodeId) : [...pinnedIds, nodeId];
    setPinBusy(nodeId);
    setStatus(null);
    try {
      await setPinnedIds(next);
    } catch (error) {
      setStatus(error instanceof Error ? error.message : "Could not update pinned entities.");
    } finally {
      setPinBusy(null);
    }
  }

  return (
    <div className="space-y-3">
      <p className="text-xs text-zinc-500">
        Search and filter the graph, pin key entities so they stay visible, then click a node or edge to open cited sources. Drag nodes to rearrange; pan and zoom to explore.
      </p>
      <div className="space-y-3 rounded-lg border border-zinc-200 p-3 dark:border-zinc-800">
        <div className="space-y-1">
          <label htmlFor={searchId} className="text-xs font-medium text-zinc-600 dark:text-zinc-300">
            Search entities and relationships
          </label>
          <input
            id={searchId}
            type="search"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="Label, type, or relationship…"
            className="w-full rounded border border-zinc-300 bg-white px-2 py-1.5 text-sm dark:border-zinc-700 dark:bg-zinc-950"
          />
        </div>
        <TypeFilter label="Node types" options={nodeTypeOptions} selected={nodeTypes} onChange={setNodeTypes} />
        <TypeFilter label="Edge types" options={edgeTypeOptions} selected={edgeTypes} onChange={setEdgeTypes} />
        {(filtered.active || pinnedIds.length > 0) && (
          <p className="text-xs text-zinc-500" role="status">
            Showing {filtered.nodes.length} of {graph.nodes.length} entities
            {pinnedIds.length > 0 ? ` · ${pinnedIds.length} pinned` : ""}.
            {filtered.active && (
              <button
                type="button"
                className="ml-2 underline"
                onClick={() => {
                  setQuery("");
                  setNodeTypes([]);
                  setEdgeTypes([]);
                }}
              >
                Clear filters
              </button>
            )}
          </p>
        )}
      </div>
      <ul aria-label="Relationship status legend" className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-zinc-600 dark:text-zinc-300">
        {Object.entries(STATUS_EDGE).map(([value, color]) => (
          <li key={value} className="flex items-center gap-1.5 capitalize">
            <span aria-hidden="true" className="inline-block h-0.5 w-4" style={{ backgroundColor: color }} />
            {value}
          </li>
        ))}
      </ul>
      <div className="h-[min(28rem,55vh)] min-h-[18rem] overflow-hidden rounded-lg border border-zinc-200 dark:border-zinc-800">
        <RelationshipGraphCanvas
          key={canvasKey}
          nodes={filtered.nodes}
          edges={drawable}
          pinnedIds={pinnedIds}
          onInspectNode={onInspectNode}
          onSelectEdge={onSelectEdge}
        />
      </div>
      {graph.nodes.length > 0 && (
        <ul aria-label="Pin entities" className="flex flex-wrap gap-2">
          {graph.nodes.map((node) => {
            const pinned = pinnedIds.includes(node.id);
            const hidden = filtered.active && !filtered.nodes.some((item) => item.id === node.id);
            if (hidden && !pinned) return null;
            return (
              <li key={node.id}>
                <button
                  type="button"
                  aria-pressed={pinned}
                  disabled={pinBusy === node.id}
                  onClick={() => void togglePin(node.id)}
                  className={`rounded border px-2 py-1 text-xs ${
                    pinned
                      ? "border-amber-400 bg-amber-50 text-amber-900 dark:border-amber-700 dark:bg-amber-950 dark:text-amber-100"
                      : "border-zinc-300 text-zinc-700 dark:border-zinc-700 dark:text-zinc-200"
                  }`}
                >
                  {pinned ? "Unpin" : "Pin"} {node.label}
                </button>
              </li>
            );
          })}
        </ul>
      )}
      {status && (
        <p role="status" className="text-xs text-amber-800 dark:text-amber-300">
          {status}
        </p>
      )}
      {pair && (
        <section aria-labelledby={pairTitleId} className="space-y-2 rounded-lg border border-blue-200 p-3 dark:border-blue-900">
          <div className="flex items-start justify-between gap-3">
            <h3 id={pairTitleId} ref={pairHeading} tabIndex={-1} className="break-words text-sm font-semibold">
              {pair.length} relationships between {name(pair[0].source)} and {name(pair[0].target)}
            </h3>
            <button type="button" onClick={() => setPair(null)} className="rounded border border-zinc-300 px-2 py-1 text-xs dark:border-zinc-700">
              Close
            </button>
          </div>
          <ul className="space-y-2">
            {pair.map((edge) => (
              <li key={edge.id} data-pair-edge={edge.id} className="flex flex-wrap items-center justify-between gap-2 text-sm">
                <span className="flex flex-wrap items-center gap-2 break-words">
                  <span>
                    {name(edge.source)} <span aria-label="to">→</span> {name(edge.target)}: <span className="capitalize">{relationLabel(edge.type)}</span>
                  </span>
                  <Status value={edge.status} />
                </span>
                <button type="button" aria-haspopup="dialog" onClick={() => inspect(edge.evidence_ids, relationLabel(edge.type))}
                  className="rounded-md border border-blue-200 bg-blue-50 px-2 py-1 text-xs text-blue-800 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-blue-600 dark:border-blue-800 dark:bg-blue-950 dark:text-blue-200">
                  Open {sourceCountLabel(edge.evidence_ids)}
                </button>
              </li>
            ))}
          </ul>
        </section>
      )}
      {filtered.edges.length > 0 ? (
        <ul aria-label="Relationships" className="space-y-3">
          {filtered.edges.map((edge) => (
            <li key={edge.id} data-edge-id={edge.id} className="space-y-2 rounded-lg bg-zinc-50 p-3 dark:bg-zinc-900">
              <p className="break-words text-sm font-medium">
                {name(edge.source)} <span aria-label="to">→</span> {name(edge.target)}
              </p>
              {danglingIds.has(edge.id) && (
                <p data-unknown-entity className="text-xs text-amber-800 dark:text-amber-300">
                  References an entity not in this graph, so it is not drawn above.
                </p>
              )}
              <p className="text-xs capitalize text-zinc-500">{relationLabel(edge.type)}</p>
              {edge.description && <p className="break-words text-sm leading-6">{edge.description}</p>}
              <Status value={edge.status} />
              <SourceReferences {...sourceProps} ids={edge.evidence_ids} />
            </li>
          ))}
        </ul>
      ) : (
        <p className="text-sm text-zinc-500">
          {filtered.active ? "No relationships match the current filters." : "No supported relationships."}
        </p>
      )}
      {drawerIds.length > 0 && (
        <SourceReferences
          key={openToken}
          {...sourceProps}
          ids={drawerIds}
          autoOpen
          hideTriggers
        />
      )}
    </div>
  );
}
