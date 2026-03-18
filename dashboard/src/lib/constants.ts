export const WS_URL = process.env.NEXT_PUBLIC_WS_URL || "ws://localhost:8000/ws";
export const API_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

export const COGNITIVE_STATUS_LABELS: Record<string, string> = {
  idle: "Idle",
  connected: "Connected",
  analyzing_goal: "Analyzing Goal",
  planning: "Creating Plan",
  critiquing_plan: "Reviewing Plan",
  reasoning: "Thinking",
  deciding_action: "Deciding Action",
  waiting_confirmation: "Waiting for Confirmation",
  executing_action: "Executing Action",
  evaluating: "Evaluating Result",
  self_critiquing: "Self-Critiquing",
  retrying: "Retrying",
  replanning: "Replanning",
  asking_user: "Asking for Input",
  completed: "Completed",
  failed: "Failed",
};

export const COGNITIVE_STATUS_COLORS: Record<string, string> = {
  idle: "text-gray-400",
  connected: "text-green-400",
  analyzing_goal: "text-blue-400",
  planning: "text-purple-400",
  critiquing_plan: "text-purple-300",
  reasoning: "text-yellow-400",
  deciding_action: "text-orange-400",
  waiting_confirmation: "text-amber-400",
  executing_action: "text-cyan-400",
  evaluating: "text-teal-400",
  self_critiquing: "text-pink-400",
  retrying: "text-red-300",
  replanning: "text-purple-300",
  asking_user: "text-amber-400",
  completed: "text-green-400",
  failed: "text-red-400",
};
