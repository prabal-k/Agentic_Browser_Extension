"use client";

import { useAgentStore } from "@/stores/agent-store";
import { cn } from "@/lib/utils";
import { COGNITIVE_STATUS_LABELS } from "@/lib/constants";
import { Bot } from "lucide-react";

export function FloatingBubble({ onClick }: { onClick?: () => void }) {
  const { connectionStatus, cognitiveStatus } = useAgentStore();

  const isActive =
    cognitiveStatus !== "idle" &&
    cognitiveStatus !== "connected" &&
    cognitiveStatus !== "completed" &&
    cognitiveStatus !== "failed";

  const isDone = cognitiveStatus === "completed";
  const isFailed = cognitiveStatus === "failed";

  return (
    <button
      onClick={onClick}
      className={cn(
        "fixed bottom-6 right-6 w-14 h-14 rounded-full shadow-lg",
        "flex items-center justify-center transition-all duration-300",
        "hover:scale-110 active:scale-95 z-50",
        connectionStatus !== "connected" && "bg-gray-700",
        connectionStatus === "connected" && !isActive && !isDone && !isFailed && "bg-blue-600",
        isActive && "bg-purple-600 animate-pulse",
        isDone && "bg-green-600",
        isFailed && "bg-red-600"
      )}
      title={
        connectionStatus !== "connected"
          ? "Disconnected"
          : COGNITIVE_STATUS_LABELS[cognitiveStatus] || cognitiveStatus
      }
    >
      <Bot size={24} className="text-white" />

      {/* Activity ring */}
      {isActive && (
        <div className="absolute inset-0 rounded-full border-2 border-purple-400 animate-ping opacity-30" />
      )}
    </button>
  );
}
