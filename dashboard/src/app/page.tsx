"use client";

import { useEffect } from "react";
import { AgentStatus } from "@/components/agent-status";
import { ChatInterface } from "@/components/chat-interface";
import { InterruptPanel } from "@/components/interrupt-panel";
import { DomViewer } from "@/components/dom-viewer";
import { SettingsPanel } from "@/components/settings-panel";
import { useWebSocket } from "@/hooks/use-websocket";
import { useAgentStore } from "@/stores/agent-store";
import { Wifi } from "lucide-react";

export default function DashboardPage() {
  const { connect, isConnected } = useWebSocket();
  const connectionStatus = useAgentStore((s) => s.connectionStatus);
  const showDomViewer = useAgentStore((s) => s.showDomViewer);

  // Auto-connect on mount
  useEffect(() => {
    connect();
  }, [connect]);

  return (
    <div className="h-screen flex flex-col bg-[#0a0a0a]">
      {/* Top bar */}
      <div className="flex items-center justify-between px-4 py-2 bg-gray-900 border-b border-gray-800">
        <div className="flex items-center gap-3">
          <h1 className="text-sm font-semibold text-gray-200">
            Agentic Browser
          </h1>
          <span className="text-xs text-gray-600">Test Dashboard</span>
        </div>
        <div className="flex items-center gap-2">
          {!isConnected && connectionStatus !== "connecting" && (
            <button
              onClick={connect}
              className="flex items-center gap-1.5 px-3 py-1 bg-green-700 hover:bg-green-600
                         text-white rounded text-xs transition-colors"
            >
              <Wifi size={12} />
              Connect
            </button>
          )}
          <DomViewer />
          <div className="relative">
            <SettingsPanel />
          </div>
        </div>
      </div>

      {/* Agent status bar */}
      <AgentStatus />

      {/* Main content */}
      <div className="flex-1 flex overflow-hidden">
        {/* Chat + Interrupt */}
        <div className="flex-1 flex flex-col">
          <ChatInterface />
          <InterruptPanel />
        </div>

        {/* DOM Viewer sidebar */}
        {showDomViewer && <DomViewer />}
      </div>
    </div>
  );
}
