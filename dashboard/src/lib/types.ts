/** WebSocket message types matching the backend protocol */

export type WSMessageType =
  | "server_status"
  | "server_reasoning"
  | "server_plan"
  | "server_action_request"
  | "server_evaluation"
  | "server_interrupt"
  | "server_done"
  | "server_error"
  | "client_goal"
  | "client_user_response"
  | "client_action_result"
  | "client_dom_update"
  | "client_cancel";

export type ConnectionStatus = "disconnected" | "connecting" | "connected" | "error";

export type CognitiveStatus =
  | "idle"
  | "connected"
  | "analyzing_goal"
  | "planning"
  | "critiquing_plan"
  | "reasoning"
  | "deciding_action"
  | "waiting_confirmation"
  | "executing_action"
  | "evaluating"
  | "self_critiquing"
  | "retrying"
  | "replanning"
  | "asking_user"
  | "completed"
  | "failed";

// --- Server Messages ---

export interface ServerStatus {
  type: "server_status";
  timestamp: number;
  cognitive_status: CognitiveStatus;
  message: string;
  session_id: string;
  iteration?: number;
}

export interface ServerReasoning {
  type: "server_reasoning";
  timestamp: number;
  content: string;
  reasoning_type: "goal_analysis" | "thinking";
  is_streaming: boolean;
  is_final: boolean;
  session_id: string;
  sub_goals?: string[];
  complexity?: string;
}

export interface PlanStep {
  step_id: number;
  description: string;
  status: string;
  expected_outcome: string;
}

export interface ServerPlan {
  type: "server_plan";
  timestamp: number;
  steps: PlanStep[];
  plan_version: number;
  session_id: string;
}

export interface ActionDetails {
  action_id: string;
  action_type: string;
  element_id: number | null;
  value: string;
  description: string;
  reasoning: string;
  confidence: number;
  risk_level: string;
}

export interface ServerActionRequest {
  type: "server_action_request";
  timestamp: number;
  action: ActionDetails;
  requires_confirmation: boolean;
  execute?: boolean;
  step_number?: number;
  total_steps?: number;
  session_id: string;
}

export interface ServerEvaluation {
  type: "server_evaluation";
  timestamp: number;
  action_succeeded: boolean;
  progress_percentage: number;
  summary: string;
  next_step: string;
  session_id: string;
}

export interface InterruptField {
  field_id: string;
  field_type: "text" | "confirm" | "select";
  label: string;
  description?: string;
  options?: string[];
}

export interface ServerInterrupt {
  type: "server_interrupt";
  timestamp: number;
  interrupt_id: string;
  title: string;
  context: string;
  fields: InterruptField[];
  urgency: "normal" | "warning" | "critical";
  session_id: string;
}

export interface ServerDone {
  type: "server_done";
  timestamp: number;
  success: boolean;
  summary: string;
  steps_completed: number;
  steps_total: number;
  total_actions: number;
  session_id: string;
}

export interface ServerError {
  type: "server_error";
  timestamp: number;
  message: string;
  recoverable: boolean;
  session_id?: string;
}

export type ServerMessage =
  | ServerStatus
  | ServerReasoning
  | ServerPlan
  | ServerActionRequest
  | ServerEvaluation
  | ServerInterrupt
  | ServerDone
  | ServerError;

// --- Chat Display ---

export interface ChatMessage {
  id: string;
  role: "user" | "agent" | "system";
  content: string;
  timestamp: number;
  metadata?: {
    type?: WSMessageType;
    plan?: PlanStep[];
    action?: ActionDetails;
    evaluation?: { succeeded: boolean; progress: number; summary: string };
    interrupt?: { id: string; title: string; fields: InterruptField[]; urgency: string };
    done?: { success: boolean; summary: string; steps: number; actions: number };
    export?: { available: boolean; id: string; formats: string[]; items: number };
    error?: { message: string; recoverable: boolean };
  };
}

// --- DOM Types (matching backend PageContext) ---

export interface BoundingBox {
  x: number;
  y: number;
  width: number;
  height: number;
}

export interface DOMElement {
  element_id: number;
  element_type: string;
  tag_name: string;
  text: string;
  attributes: Record<string, string>;
  is_visible: boolean;
  is_enabled: boolean;
  is_focused?: boolean;
  bounding_box?: BoundingBox;
  parent_context?: string;
  children_count?: number;
  css_selector?: string;
  xpath?: string;
}

export interface DOMSnapshot {
  url: string;
  title: string;
  timestamp: number;
  viewport_width: number;
  viewport_height: number;
  scroll_position: number;
  has_more_content_below: boolean;
  meta_description?: string;
  page_text_summary?: string;
  elements: DOMElement[];
  forms?: { name: string; action: string; method: string; field_ids: number[] }[];
  navigation?: { label: string; element_ids: number[] }[];
}
