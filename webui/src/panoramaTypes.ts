import type { AiEngineeringDocumentSlug } from "./aiEngineeringApi";

export type PanoramaActionId = "brain" | "agents" | "missions" | "sessions" | "operations" | "review" | "activity" | "identity" | "governance" | "access" | "account" | "agent-admin" | "notes" | "hr" | "office" | "voc" | "fae";
export type PanoramaLayerKind = "industry" | "portfolio" | "workflow" | "support";
export type PanoramaGroupRole = "upstream" | "company" | "downstream" | "products" | "technology" | "marketing" | "delivery" | "support";
export type PanoramaEdgeKind = "supply" | "supports" | "feedback";

export interface PanoramaNode {
  id: string;
  title: string;
  subtitle: string;
  detail: string[];
  actions: PanoramaActionId[];
  source_ids: string[];
}

export interface PanoramaGroup {
  id: string;
  title: string;
  role: PanoramaGroupRole;
  columns: number;
  node_ids: string[];
}

export interface PanoramaLayer {
  id: string;
  title: string;
  kind: PanoramaLayerKind;
  groups: PanoramaGroup[];
}

export interface PanoramaEdge {
  id: string;
  from: string;
  to: string;
  kind: PanoramaEdgeKind;
  label: string;
}

export interface PanoramaSource {
  id: string;
  label: string;
  document: AiEngineeringDocumentSlug;
}

export interface PanoramaData {
  version: string;
  updated_at: string;
  title: string;
  layers: PanoramaLayer[];
  nodes: PanoramaNode[];
  edges: PanoramaEdge[];
  sources: PanoramaSource[];
}

export interface PanoramaEditorState {
  revision: number;
  published: PanoramaData;
  draft: PanoramaData | null;
  previous: PanoramaData | null;
}
