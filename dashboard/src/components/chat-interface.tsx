"use client";

import { useRef, useEffect, useState } from "react";
import { useAgentStore } from "@/stores/agent-store";
import { useWebSocket } from "@/hooks/use-websocket";
import { cn, formatTimestamp } from "@/lib/utils";
import { Send, Square } from "lucide-react";
import type { ChatMessage } from "@/lib/types";

function PlanDisplay({ steps }: { steps: NonNullable<NonNullable<ChatMessage["metadata"]>["plan"]> }) {
  return (
    <div className="mt-2 space-y-1">
      {steps.map((step, i) => (
        <div key={step.step_id} className="flex items-start gap-2 text-sm">
          <span className={cn(
            "w-5 h-5 rounded-full flex items-center justify-center text-xs font-mono shrink-0 mt-0.5",
            step.status === "completed" ? "bg-green-900 text-green-300" :
            step.status === "in_progress" ? "bg-blue-900 text-blue-300" :
            "bg-gray-800 text-gray-400"
          )}>
            {i + 1}
          </span>
          <div>
            <p className="text-gray-300">{step.description}</p>
            {step.expected_outcome && (
              <p className="text-gray-500 text-xs mt-0.5">{step.expected_outcome}</p>
            )}
          </div>
        </div>
      ))}
    </div>
  );
}

function ActionDisplay({ action }: { action: NonNullable<ChatMessage["metadata"]>["action"] }) {
  if (!action) return null;
  return (
    <div className="mt-2 rounded bg-gray-800/50 border border-gray-700 p-3 text-sm">
      <div className="flex items-center justify-between">
        <span className="font-medium text-orange-300">{action.action_type}</span>
        <span className={cn(
          "text-xs px-2 py-0.5 rounded",
          action.confidence >= 0.8 ? "bg-green-900/50 text-green-300" :
          action.confidence >= 0.5 ? "bg-yellow-900/50 text-yellow-300" :
          "bg-red-900/50 text-red-300"
        )}>
          {Math.round(action.confidence * 100)}% confidence
        </span>
      </div>
      {action.element_id != null && (
        <p className="text-gray-400 mt-1">Element: [{action.element_id}]</p>
      )}
      {action.value && (
        <p className="text-gray-400 mt-1">Value: &quot;{action.value}&quot;</p>
      )}
      {action.reasoning && (
        <p className="text-gray-500 text-xs mt-2 italic">{action.reasoning}</p>
      )}
    </div>
  );
}

function EvalDisplay({ evaluation }: { evaluation: NonNullable<ChatMessage["metadata"]>["evaluation"] }) {
  if (!evaluation) return null;
  return (
    <div className="mt-2 flex items-center gap-3 text-sm">
      <span className={evaluation.succeeded ? "text-green-400" : "text-red-400"}>
        {evaluation.succeeded ? "Success" : "Failed"}
      </span>
      <div className="flex-1 h-1.5 bg-gray-800 rounded-full overflow-hidden">
        <div
          className="h-full bg-blue-500 rounded-full transition-all"
          style={{ width: `${evaluation.progress}%` }}
        />
      </div>
      <span className="text-gray-400 text-xs">{evaluation.progress}%</span>
    </div>
  );
}

function DoneDisplay({ done }: { done: NonNullable<ChatMessage["metadata"]>["done"] }) {
  if (!done) return null;
  return (
    <div className={cn(
      "mt-2 rounded p-3 text-sm border",
      done.success
        ? "bg-green-950/30 border-green-800 text-green-300"
        : "bg-red-950/30 border-red-800 text-red-300"
    )}>
      <p className="font-medium">{done.success ? "Task Completed" : "Task Failed"}</p>
      <p className="text-xs mt-1 opacity-80">
        {done.steps} steps completed, {done.actions} actions executed
      </p>
    </div>
  );
}

function MessageBubble({ msg }: { msg: ChatMessage }) {
  const isUser = msg.role === "user";
  const isSystem = msg.role === "system";
  const meta = msg.metadata;

  return (
    <div className={cn("flex", isUser ? "justify-end" : "justify-start")}>
      <div
        className={cn(
          "max-w-[85%] rounded-lg px-4 py-2.5",
          isUser && "bg-blue-600 text-white",
          msg.role === "agent" && "bg-gray-800 text-gray-200",
          isSystem && meta?.type === "server_error"
            ? "bg-red-950/50 text-red-300 border border-red-800"
            : isSystem
            ? "bg-gray-800/50 text-gray-400 border border-gray-700"
            : ""
        )}
      >
        {/* Label */}
        <div className="flex items-center gap-2 mb-1">
          <span className={cn(
            "text-xs font-medium",
            isUser ? "text-blue-200" : isSystem ? "text-gray-500" : "text-purple-400"
          )}>
            {isUser ? "You" : isSystem ? "System" : "Agent"}
          </span>
          <span className="text-xs text-gray-600">{formatTimestamp(msg.timestamp)}</span>
        </div>

        {/* Content */}
        <p className="text-sm whitespace-pre-wrap">{msg.content}</p>

        {/* Rich metadata displays */}
        {meta?.plan && <PlanDisplay steps={meta.plan} />}
        {meta?.action && <ActionDisplay action={meta.action} />}
        {meta?.evaluation && <EvalDisplay evaluation={meta.evaluation} />}
        {meta?.done && <DoneDisplay done={meta.done} />}
      </div>
    </div>
  );
}

export function ChatInterface() {
  const [input, setInput] = useState("");
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const { messages, cognitiveStatus, connectionStatus } = useAgentStore();
  const { sendGoal, cancelTask, isConnected } = useWebSocket();
  const domSnapshot = useAgentStore((s) => s.domSnapshot);

  const isWorking =
    cognitiveStatus !== "idle" &&
    cognitiveStatus !== "connected" &&
    cognitiveStatus !== "completed" &&
    cognitiveStatus !== "failed";

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    const goal = input.trim();
    if (!goal || !isConnected) return;
    sendGoal(goal, domSnapshot ?? undefined);
    setInput("");
  };

  return (
    <div className="flex flex-col h-full">
      {/* Messages area */}
      <div className="flex-1 overflow-y-auto p-4 space-y-3">
        {messages.length === 0 && (
          <div className="flex items-center justify-center h-full text-gray-600">
            <div className="text-center">
              <p className="text-lg font-medium">Agentic Browser Extension</p>
              <p className="text-sm mt-1">
                {isConnected
                  ? "Enter a goal to get started"
                  : "Connect to the server first"}
              </p>
            </div>
          </div>
        )}
        {messages.map((msg) => (
          <MessageBubble key={msg.id} msg={msg} />
        ))}
        <div ref={messagesEndRef} />
      </div>

      {/* Input area */}
      <form
        onSubmit={handleSubmit}
        className="border-t border-gray-800 p-3 flex gap-2"
      >
        <input
          type="text"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder={
            !isConnected
              ? "Connect to server first..."
              : isWorking
              ? "Agent is working..."
              : "Enter your goal..."
          }
          disabled={!isConnected}
          className="flex-1 bg-gray-800 text-gray-200 rounded-lg px-4 py-2.5 text-sm
                     placeholder-gray-500 border border-gray-700 focus:border-blue-500
                     focus:outline-none disabled:opacity-50"
        />
        {isWorking ? (
          <button
            type="button"
            onClick={cancelTask}
            className="px-4 py-2.5 bg-red-600 hover:bg-red-700 text-white rounded-lg
                       transition-colors flex items-center gap-1.5 text-sm"
          >
            <Square size={14} />
            Stop
          </button>
        ) : (
          <button
            type="submit"
            disabled={!isConnected || !input.trim()}
            className="px-4 py-2.5 bg-blue-600 hover:bg-blue-700 text-white rounded-lg
                       transition-colors disabled:opacity-50 disabled:cursor-not-allowed
                       flex items-center gap-1.5 text-sm"
          >
            <Send size={14} />
            Send
          </button>
        )}
      </form>
    </div>
  );
}
