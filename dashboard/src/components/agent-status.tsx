"use client";

import { useAgentStore } from "@/stores/agent-store";
import { COGNITIVE_STATUS_LABELS, COGNITIVE_STATUS_COLORS } from "@/lib/constants";
import { cn } from "@/lib/utils";

export function AgentStatus() {
  const { connectionStatus, cognitiveStatus, iteration, sessionId } =
    useAgentStore();

  const isActive =
    cognitiveStatus !== "idle" &&
    cognitiveStatus !== "connected" &&
    cognitiveStatus !== "completed" &&
    cognitiveStatus !== "failed";

  return (
    <div className="flex items-center gap-3 px-4 py-2 bg-gray-900 border-b border-gray-800 text-sm">
      {/* Connection indicator */}
      <div className="flex items-center gap-1.5">
        <div
          className={cn(
            "w-2 h-2 rounded-full",
            connectionStatus === "connected" && "bg-green-400",
            connectionStatus === "connecting" && "bg-yellow-400 animate-pulse",
            connectionStatus === "disconnected" && "bg-gray-500",
            connectionStatus === "error" && "bg-red-400"
          )}
        />
        <span className="text-gray-400 capitalize">{connectionStatus}</span>
      </div>

      {/* Separator */}
      <div className="w-px h-4 bg-gray-700" />

      {/* Cognitive status */}
      <div className="flex items-center gap-1.5">
        {isActive && (
          <div className="w-1.5 h-1.5 rounded-full bg-blue-400 animate-pulse" />
        )}
        <span className={cn(COGNITIVE_STATUS_COLORS[cognitiveStatus] || "text-gray-400")}>
          {COGNITIVE_STATUS_LABELS[cognitiveStatus] || cognitiveStatus}
        </span>
      </div>

      {/* Iteration counter */}
      {iteration > 0 && (
        <>
          <div className="w-px h-4 bg-gray-700" />
          <span className="text-gray-500">Iteration {iteration}</span>
        </>
      )}

      {/* Session ID */}
      {sessionId && (
        <span className="ml-auto text-gray-600 font-mono text-xs">
          {sessionId}
        </span>
      )}
    </div>
  );
}
