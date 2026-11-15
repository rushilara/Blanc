"use client";

import { Component, useMemo, useRef, useState, type ReactNode } from "react";
import type { EvidenceRecord } from "@/lib/analysis";
import { readNodeConnections, type MissedNote } from "@/lib/connection-reading";
import { supportChanged, supportLabel, type SupportStatus } from "@/lib/comparison";
import { Canvas } from "@react-three/fiber";
import { Billboard, Line, OrbitControls, Text } from "@react-three/drei";
import type { ThreeEvent } from "@react-three/fiber";

type MapNode = { id: string; label: string; type: string };
type MapEdge = {
  id: string;
  source: string;
  target: string;
  type: string;
  description?: string;
  status: string;
  evidence_ids: string[];
};

const INK = "#372416";
const BURGUNDY = "#822b33";
const PAPER = "#e0d9c8";

const NODE_COLOR: Record<string, string> = {
  person: "#372416",
  location: "#822b33",
  account: "#5c4a3a",
  organization: "#6e5344",
  device: "#8a5a12",
  document: "#3f5344",
  other: "#7a6a5a",
};

type Vec = [number, number, number];

/** The busiest name sits in the middle. Everyone else stands on a ring, evenly spaced. */
function arrange(nodes: MapNode[], edges: MapEdge[]): Map<string, Vec> {
  const degree = new Map<string, number>();
  for (const edge of edges) {
    degree.set(edge.source, (degree.get(edge.source) ?? 0) + 1);
    degree.set(edge.target, (degree.get(edge.target) ?? 0) + 1);
  }
  const ordered = [...nodes].sort((a, b) => (degree.get(b.id) ?? 0) - (degree.get(a.id) ?? 0) || a.label.localeCompare(b.label));
  const pos = new Map<string, Vec>();
  if (ordered.length === 0) return pos;
  const [hub, ...rest] = ordered;
  pos.set(hub.id, [0, 0, 0]);
  rest.forEach((node, index) => {
    const angle = (index / rest.length) * Math.PI * 2 - Math.PI / 2;
    pos.set(node.id, [Math.cos(angle) * 1.25, Math.sin(angle) * 0.95, Math.sin(angle * 2) * 0.28]);
  });
  return pos;
}

function Marker({
  id,
  label,
  type,
  position,
  radius,
  selected,
  dim,
  onSelect,
}: {
  id: string;
  label: string;
  type: string;
  position: Vec;
  radius: number;
  selected: boolean;
  dim: boolean;
  onSelect: (id: string) => void;
}) {
  return <group position={position}>
    <mesh
      onClick={(event: ThreeEvent<MouseEvent>) => {
        event.stopPropagation();
        onSelect(id);
      }}
      onPointerOver={(event: ThreeEvent<PointerEvent>) => {
        event.stopPropagation();
        document.body.style.cursor = "pointer";
      }}
      onPointerOut={() => {
        document.body.style.cursor = "";
      }}
    >
      <sphereGeometry args={[selected ? radius + 0.05 : radius, 32, 32]} />
      <meshStandardMaterial
        color={NODE_COLOR[type] ?? "#7a6a5a"}
        emissive={selected ? BURGUNDY : "#000000"}
        emissiveIntensity={selected ? 0.4 : 0}
        roughness={0.42}
      />
    </mesh>
    <mesh
      onClick={(event: ThreeEvent<MouseEvent>) => {
        event.stopPropagation();
        onSelect(id);
      }}
      onPointerOver={(event: ThreeEvent<PointerEvent>) => {
        event.stopPropagation();
        document.body.style.cursor = "pointer";
      }}
      onPointerOut={() => {
        document.body.style.cursor = "";
      }}
    >
      <sphereGeometry args={[0.42, 12, 12]} />
      <meshBasicMaterial transparent opacity={0} depthWrite={false} />
    </mesh>
    <Billboard position={[0, radius + 0.2, 0]}>
      <Text
        font="/blanc/fonts/LibreBaskerville-Regular.ttf"
        fontSize={0.2}
        color={selected ? BURGUNDY : INK}
        anchorX="center"
        anchorY="bottom"
        maxWidth={1.3}
        textAlign="center"
        fillOpacity={dim ? 0.35 : 1}
        outlineWidth={0.008}
        outlineColor={PAPER}
        outlineOpacity={dim ? 0.35 : 0.9}
      >
        {label}
      </Text>
    </Billboard>
  </group>;
}

function Scene({
  nodes,
  edges,
  positions,
  selected,
  neighbors,
  changed,
  onSelect,
}: {
  nodes: MapNode[];
  edges: MapEdge[];
  positions: Map<string, Vec>;
  selected: string | null;
  neighbors: Set<string>;
  changed: Set<string>;
  onSelect: (id: string | null) => void;
}) {
  const degree = useMemo(() => {
    const counts = new Map<string, number>();
    for (const edge of edges) {
      counts.set(edge.source, (counts.get(edge.source) ?? 0) + 1);
      counts.set(edge.target, (counts.get(edge.target) ?? 0) + 1);
    }
    return counts;
  }, [edges]);
  const reduceMotion = typeof window !== "undefined" && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  return <>
    <ambientLight intensity={0.9} />
    <directionalLight position={[4, 6, 5]} intensity={1.15} />
    <directionalLight position={[-4, -2, -3]} intensity={0.35} />
    {edges.map(edge => {
      const from = positions.get(edge.source);
      const to = positions.get(edge.target);
      if (!from || !to) return null;
      const active = !selected || edge.source === selected || edge.target === selected;
      const marked = changed.has(edge.id);
      return <Line
        key={edge.id}
        points={[from, to]}
        color={marked ? "#8a5a12" : active && selected ? BURGUNDY : "#8d7b68"}
        lineWidth={marked || (active && selected) ? 2.6 : 1}
        transparent
        opacity={active ? 0.95 : 0.16}
      />;
    })}
    {nodes.map(node => {
      const position = positions.get(node.id);
      if (!position) return null;
      const linked = (degree.get(node.id) ?? 0);
      return <Marker
        key={node.id}
        id={node.id}
        label={node.label}
        type={node.type}
        position={position}
        radius={0.1 + Math.min(linked, 6) * 0.018}
        selected={node.id === selected}
        dim={!!selected && node.id !== selected && !neighbors.has(node.id)}
        onSelect={id => onSelect(id === selected ? null : id)}
      />;
    })}
    <OrbitControls
      makeDefault
      enablePan={false}
      enableDamping
      dampingFactor={0.22}
      rotateSpeed={reduceMotion ? 0.35 : 0.42}
      zoomSpeed={0.28}
      minDistance={5.8}
      maxDistance={8}
      minPolarAngle={Math.PI / 4.2}
      maxPolarAngle={Math.PI / 1.85}
    />
  </>;
}

export function ConnectionMap({
  nodes,
  edges,
  records,
  claims,
  events,
  questions,
  cite,
  edgeSupport,
}: {
  nodes: MapNode[];
  edges: MapEdge[];
  records: EvidenceRecord[];
  claims: { id: string; text: string; status: string; evidence_ids: string[] }[];
  events: { id: string; description: string; status: string; evidence_ids: string[] }[];
  questions: string[];
  cite: (id: string) => ReactNode;
  edgeSupport?: Record<string, SupportStatus>;
}) {
  const [selected, setSelected] = useState<string | null>(null);
  const [failed, setFailed] = useState(false);
  const drag = useRef({ x: 0, y: 0, moved: false });
  const changed = useMemo(() => new Set(Object.entries(edgeSupport ?? {}).filter(([, status]) => supportChanged(status)).map(([id]) => id)), [edgeSupport]);
  const positions = useMemo(() => arrange(nodes, edges), [nodes, edges]);
  const labels = useMemo(() => new Map(nodes.map(node => [node.id, node.label])), [nodes]);
  const neighbors = useMemo(() => {
    const next = new Set<string>();
    if (!selected) return next;
    for (const edge of edges) {
      if (edge.source === selected) next.add(edge.target);
      if (edge.target === selected) next.add(edge.source);
    }
    return next;
  }, [edges, selected]);
  const chosen = nodes.find(node => node.id === selected);
  const reading = useMemo(() => {
    if (!chosen) return null;
    return readNodeConnections({
      nodeId: chosen.id,
      label: chosen.label,
      name: id => labels.get(id) ?? id,
      edges,
      records,
      claims,
      events: events.map(event => ({ id: event.id, text: event.description, status: event.status, evidence_ids: event.evidence_ids })),
      questions,
    });
  }, [chosen, labels, edges, records, claims, events, questions]);

  if (nodes.length === 0) return <p>No connections in this revision.</p>;
  if (failed) {
    return <ul className="blanc-map-fallback">{edges.map(edge => <li key={edge.id}>{labels.get(edge.source) ?? edge.source} → {labels.get(edge.target) ?? edge.target}</li>)}</ul>;
  }

  return <div className="blanc-map-frame">
    <div className="blanc-map" onPointerLeave={() => { document.body.style.cursor = ""; }}>
      <MapBoundary onFail={() => setFailed(true)}>
        <Canvas
          camera={{ position: [0, 0.15, 6.6], fov: 40 }}
          gl={{ alpha: true, antialias: true }}
          style={{ width: "100%", height: "100%", background: "transparent", display: "block" }}
          onPointerDown={event => { drag.current = { x: event.clientX, y: event.clientY, moved: false }; }}
          onPointerMove={event => {
            if (Math.hypot(event.clientX - drag.current.x, event.clientY - drag.current.y) > 5) drag.current.moved = true;
          }}
          onPointerMissed={() => { if (!drag.current.moved) setSelected(null); }}
        >
          <Scene nodes={nodes} edges={edges} positions={positions} selected={selected} neighbors={neighbors} changed={changed} onSelect={setSelected} />
        </Canvas>
      </MapBoundary>
    </div>
    <div className="blanc-map-foot">
      {chosen && reading ? <section aria-label={`Connections for ${chosen.label}`}>
        <h2>{chosen.label}</h2>
        <p className="blanc-map-role">{chosen.type}</p>
        {reading.connections.length === 0 && <p>Nothing else in this revision connects to {chosen.label}.</p>}
        {reading.connections.map(item => {
          const support = edgeSupport?.[item.edgeId];
          return <div key={item.edgeId} className={`blanc-map-link${support && supportChanged(support) ? " is-changed" : ""}`}>
          <p>
            <span className={`blanc-status blanc-status-${item.status}`}>{item.status.replaceAll("_", " ")}</span>
            {support && <span className={`blanc-support blanc-support-${support}`}>{supportLabel(support)}</span>}
            {item.otherLabel}
          </p>
          {item.stated && <p className="blanc-map-fact">{item.stated}</p>}
          {item.facts.map(fact => <p key={fact.line} className="blanc-map-fact">{fact.line}{" "}
            {fact.evidenceIds.length > 0 && <sup>{fact.evidenceIds.map(id => cite(id))}</sup>}
          </p>)}
        </div>;
        })}
        {(reading.missed.length > 0 || reading.hiddenSources > 0) && <div className="blanc-map-missed">
          <h3>Not drawn from {chosen.label}</h3>
          <p className="blanc-map-note">These mention {chosen.label} and are not a line on the map.</p>
          {reading.missed.map(note => <Missed key={note.id} note={note} cite={cite} />)}
          {reading.hiddenSources > 0 && <p className="blanc-map-note">{reading.hiddenSources} more {reading.hiddenSources === 1 ? "record mentions" : "records mention"} {chosen.label} and {reading.hiddenSources === 1 ? "is" : "are"} not connected here.</p>}
        </div>}
      </section> : <p>Drag to turn the map. Click a name to read what the records say, including anything the lines leave out.</p>}
    </div>
  </div>;
}

function Missed({ note, cite }: { note: MissedNote; cite: (id: string) => ReactNode }) {
  return <p>
    {note.status && <span className={`blanc-status blanc-status-${note.status}`}>{note.status.replaceAll("_", " ")}</span>}
    {note.text}{" "}
    {note.evidenceIds.length > 0 && <sup>{note.evidenceIds.map(id => cite(id))}</sup>}
  </p>;
}

class MapBoundary extends Component<{ children: ReactNode; onFail: () => void }, { failed: boolean }> {
  state = { failed: false };

  static getDerivedStateFromError() {
    return { failed: true };
  }

  componentDidCatch() {
    this.props.onFail();
  }

  render() {
    return this.state.failed ? null : this.props.children;
  }
}
