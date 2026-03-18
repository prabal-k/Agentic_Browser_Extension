import { create } from "zustand";
import type {
  ChatMessage,
  CognitiveStatus,
  ConnectionStatus,
  DOMSnapshot,
  PlanStep,
  ActionDetails,
  ServerMessage,
  InterruptField,
} from "@/lib/types";
import { uniqueId } from "@/lib/utils";

interface AgentState {
  // Connection
  connectionStatus: ConnectionStatus;
  sessionId: string | null;

  // Agent cognitive state
  cognitiveStatus: CognitiveStatus;
  iteration: number;

  // Chat messages
  messages: ChatMessage[];

  // Current plan
  currentPlan: PlanStep[] | null;
  planVersion: number;

  // Current action
  currentAction: ActionDetails | null;

  // Pending interrupt
  pendingInterrupt: {
    id: string;
    title: string;
    context: string;
    fields: InterruptField[];
    urgency: string;
  } | null;

  // DOM snapshot
  domSnapshot: DOMSnapshot | null;

  // Task result
  taskDone: boolean;
  taskSuccess: boolean | null;

  // Settings
  serverUrl: string;
  showDomViewer: boolean;

  // Actions
  setConnectionStatus: (status: ConnectionStatus) => void;
  setSessionId: (id: string | null) => void;
  setCognitiveStatus: (status: CognitiveStatus) => void;
  addMessage: (msg: ChatMessage) => void;
  handleServerMessage: (msg: ServerMessage) => void;
  setDomSnapshot: (dom: DOMSnapshot | null) => void;
  setPendingInterrupt: (interrupt: AgentState["pendingInterrupt"]) => void;
  setServerUrl: (url: string) => void;
  setShowDomViewer: (show: boolean) => void;
  reset: () => void;
}

const initialState = {
  connectionStatus: "disconnected" as ConnectionStatus,
  sessionId: null,
  cognitiveStatus: "idle" as CognitiveStatus,
  iteration: 0,
  messages: [],
  currentPlan: null,
  planVersion: 0,
  currentAction: null,
  pendingInterrupt: null,
  domSnapshot: null,
  taskDone: false,
  taskSuccess: null,
  serverUrl: "ws://localhost:8000/ws",
  showDomViewer: false,
};

export const useAgentStore = create<AgentState>((set, get) => ({
  ...initialState,

  setConnectionStatus: (status) => set({ connectionStatus: status }),
  setSessionId: (id) => set({ sessionId: id }),
  setCognitiveStatus: (status) => set({ cognitiveStatus: status }),
  setServerUrl: (url) => set({ serverUrl: url }),
  setShowDomViewer: (show) => set({ showDomViewer: show }),
  setDomSnapshot: (dom) => set({ domSnapshot: dom }),
  setPendingInterrupt: (interrupt) => set({ pendingInterrupt: interrupt }),

  addMessage: (msg) =>
    set((s) => ({ messages: [...s.messages, msg] })),

  handleServerMessage: (msg) => {
    const { addMessage } = get();

    switch (msg.type) {
      case "server_status":
        set({
          cognitiveStatus: msg.cognitive_status,
          sessionId: msg.session_id,
          iteration: msg.iteration ?? get().iteration,
        });
        if (msg.cognitive_status !== "connected") {
          addMessage({
            id: uniqueId(),
            role: "system",
            content: msg.message,
            timestamp: msg.timestamp,
            metadata: { type: "server_status" },
          });
        }
        break;

      case "server_reasoning":
        addMessage({
          id: uniqueId(),
          role: "agent",
          content: msg.content,
          timestamp: msg.timestamp,
          metadata: { type: "server_reasoning" },
        });
        break;

      case "server_plan":
        set({ currentPlan: msg.steps, planVersion: msg.plan_version });
        addMessage({
          id: uniqueId(),
          role: "agent",
          content: `Plan v${msg.plan_version} (${msg.steps.length} steps)`,
          timestamp: msg.timestamp,
          metadata: { type: "server_plan", plan: msg.steps },
        });
        break;

      case "server_action_request":
        set({ currentAction: msg.action });
        addMessage({
          id: uniqueId(),
          role: "agent",
          content: `Action: ${msg.action.action_type} on element ${msg.action.element_id ?? "N/A"}`,
          timestamp: msg.timestamp,
          metadata: { type: "server_action_request", action: msg.action },
        });
        break;

      case "server_evaluation":
        addMessage({
          id: uniqueId(),
          role: "agent",
          content: msg.summary,
          timestamp: msg.timestamp,
          metadata: {
            type: "server_evaluation",
            evaluation: {
              succeeded: msg.action_succeeded,
              progress: msg.progress_percentage,
              summary: msg.summary,
            },
          },
        });
        break;

      case "server_interrupt":
        set({
          pendingInterrupt: {
            id: msg.interrupt_id,
            title: msg.title,
            context: msg.context,
            fields: msg.fields,
            urgency: msg.urgency,
          },
        });
        addMessage({
          id: uniqueId(),
          role: "agent",
          content: msg.title + (msg.context ? `: ${msg.context}` : ""),
          timestamp: msg.timestamp,
          metadata: {
            type: "server_interrupt",
            interrupt: {
              id: msg.interrupt_id,
              title: msg.title,
              fields: msg.fields,
              urgency: msg.urgency,
            },
          },
        });
        break;

      case "server_done":
        set({
          taskDone: true,
          taskSuccess: msg.success,
          cognitiveStatus: msg.success ? "completed" : "failed",
          currentAction: null,
          pendingInterrupt: null,
        });
        addMessage({
          id: uniqueId(),
          role: "system",
          content: msg.summary,
          timestamp: msg.timestamp,
          metadata: {
            type: "server_done",
            done: {
              success: msg.success,
              summary: msg.summary,
              steps: msg.steps_completed,
              actions: msg.total_actions,
            },
          },
        });
        break;

      case "server_error":
        addMessage({
          id: uniqueId(),
          role: "system",
          content: msg.message,
          timestamp: msg.timestamp,
          metadata: {
            type: "server_error",
            error: { message: msg.message, recoverable: msg.recoverable },
          },
        });
        break;
    }
  },

  reset: () =>
    set({
      ...initialState,
      serverUrl: get().serverUrl,
    }),
}));
