"use client";

import { useState } from "react";
import { useAgentStore } from "@/stores/agent-store";
import { useWebSocket } from "@/hooks/use-websocket";
import { cn } from "@/lib/utils";
import { AlertTriangle, MessageSquare, CheckCircle, XCircle } from "lucide-react";

export function InterruptPanel() {
  const pendingInterrupt = useAgentStore((s) => s.pendingInterrupt);
  const { sendInterruptResponse } = useWebSocket();
  const [textValues, setTextValues] = useState<Record<string, string>>({});

  if (!pendingInterrupt) return null;

  const { title, context, fields, urgency } = pendingInterrupt;

  const handleConfirm = (fieldId: string, confirmed: boolean) => {
    sendInterruptResponse({ [fieldId]: confirmed });
    setTextValues({});
  };

  const handleTextSubmit = (fieldId: string) => {
    const value = textValues[fieldId]?.trim();
    if (!value) return;
    sendInterruptResponse({ [fieldId]: value });
    setTextValues({});
  };

  return (
    <div
      className={cn(
        "border-t p-4 space-y-3 animate-in slide-in-from-bottom",
        urgency === "warning"
          ? "bg-amber-950/30 border-amber-800"
          : urgency === "critical"
          ? "bg-red-950/30 border-red-800"
          : "bg-gray-900 border-gray-700"
      )}
    >
      {/* Header */}
      <div className="flex items-center gap-2">
        {urgency === "warning" || urgency === "critical" ? (
          <AlertTriangle
            size={16}
            className={urgency === "critical" ? "text-red-400" : "text-amber-400"}
          />
        ) : (
          <MessageSquare size={16} className="text-blue-400" />
        )}
        <h3 className="font-medium text-sm text-gray-200">{title}</h3>
      </div>

      {/* Context */}
      {context && (
        <p className="text-sm text-gray-400">{context}</p>
      )}

      {/* Fields */}
      {fields.map((field) => (
        <div key={field.field_id} className="space-y-2">
          <label className="text-sm text-gray-300">{field.label}</label>
          {field.description && (
            <p className="text-xs text-gray-500">{field.description}</p>
          )}

          {field.field_type === "confirm" && (
            <div className="flex gap-2">
              <button
                onClick={() => handleConfirm(field.field_id, true)}
                className="flex items-center gap-1.5 px-4 py-2 bg-green-700 hover:bg-green-600
                           text-white rounded text-sm transition-colors"
              >
                <CheckCircle size={14} />
                {field.options?.[0] || "Confirm"}
              </button>
              <button
                onClick={() => handleConfirm(field.field_id, false)}
                className="flex items-center gap-1.5 px-4 py-2 bg-gray-700 hover:bg-gray-600
                           text-gray-300 rounded text-sm transition-colors"
              >
                <XCircle size={14} />
                {field.options?.[1] || "Deny"}
              </button>
            </div>
          )}

          {field.field_type === "text" && (
            <div className="flex gap-2">
              <input
                type="text"
                value={textValues[field.field_id] || ""}
                onChange={(e) =>
                  setTextValues((prev) => ({ ...prev, [field.field_id]: e.target.value }))
                }
                onKeyDown={(e) => {
                  if (e.key === "Enter") handleTextSubmit(field.field_id);
                }}
                placeholder="Type your response..."
                className="flex-1 bg-gray-800 text-gray-200 rounded px-3 py-2 text-sm
                           border border-gray-700 focus:border-blue-500 focus:outline-none"
                autoFocus
              />
              <button
                onClick={() => handleTextSubmit(field.field_id)}
                className="px-4 py-2 bg-blue-600 hover:bg-blue-700 text-white rounded
                           text-sm transition-colors"
              >
                Send
              </button>
            </div>
          )}

          {field.field_type === "select" && field.options && (
            <div className="flex flex-wrap gap-2">
              {field.options.map((option) => (
                <button
                  key={option}
                  onClick={() => sendInterruptResponse({ [field.field_id]: option })}
                  className="px-3 py-1.5 bg-gray-700 hover:bg-gray-600 text-gray-300
                             rounded text-sm transition-colors"
                >
                  {option}
                </button>
              ))}
            </div>
          )}
        </div>
      ))}
    </div>
  );
}
