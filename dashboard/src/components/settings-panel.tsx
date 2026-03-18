"use client";

import { useState, useEffect } from "react";
import { useAgentStore } from "@/stores/agent-store";
import { useWebSocket } from "@/hooks/use-websocket";
import { API_URL } from "@/lib/constants";
import { Settings, Wifi, WifiOff, RefreshCw, Trash2 } from "lucide-react";

interface ServerHealth {
  status: string;
  uptime_seconds: number;
  active_sessions: number;
  ollama_url: string;
  model: string;
}

export function SettingsPanel() {
  const [open, setOpen] = useState(false);
  const [health, setHealth] = useState<ServerHealth | null>(null);
  const [loading, setLoading] = useState(false);
  const { serverUrl, setServerUrl, connectionStatus, reset } = useAgentStore();
  const { connect, disconnect, isConnected } = useWebSocket();
  const [urlInput, setUrlInput] = useState(serverUrl);

  const fetchHealth = async () => {
    setLoading(true);
    try {
      const res = await fetch(`${API_URL}/health`);
      const data = await res.json();
      setHealth(data);
    } catch {
      setHealth(null);
    }
    setLoading(false);
  };

  useEffect(() => {
    if (open) fetchHealth();
  }, [open]);

  const handleUrlSave = () => {
    if (urlInput.trim()) {
      setServerUrl(urlInput.trim());
      if (isConnected) {
        disconnect();
      }
    }
  };

  if (!open) {
    return (
      <button
        onClick={() => setOpen(true)}
        className="p-2 text-gray-400 hover:text-gray-200 transition-colors"
        title="Settings"
      >
        <Settings size={18} />
      </button>
    );
  }

  return (
    <div className="absolute top-12 right-3 w-80 bg-gray-900 border border-gray-700 rounded-lg shadow-xl z-50">
      <div className="flex items-center justify-between px-4 py-3 border-b border-gray-800">
        <h3 className="text-sm font-medium text-gray-200">Settings</h3>
        <button onClick={() => setOpen(false)} className="text-gray-400 hover:text-gray-200">
          &times;
        </button>
      </div>

      <div className="p-4 space-y-4">
        {/* Server URL */}
        <div className="space-y-2">
          <label className="text-xs text-gray-400">WebSocket URL</label>
          <div className="flex gap-2">
            <input
              type="text"
              value={urlInput}
              onChange={(e) => setUrlInput(e.target.value)}
              className="flex-1 bg-gray-800 text-gray-200 rounded px-3 py-1.5 text-sm
                         border border-gray-700 focus:border-blue-500 focus:outline-none"
            />
            <button
              onClick={handleUrlSave}
              className="px-3 py-1.5 bg-gray-700 hover:bg-gray-600 text-gray-300
                         rounded text-xs transition-colors"
            >
              Save
            </button>
          </div>
        </div>

        {/* Connection */}
        <div className="flex items-center justify-between">
          <span className="text-xs text-gray-400">Connection</span>
          {isConnected ? (
            <button
              onClick={disconnect}
              className="flex items-center gap-1.5 px-3 py-1.5 bg-red-700 hover:bg-red-600
                         text-white rounded text-xs transition-colors"
            >
              <WifiOff size={12} />
              Disconnect
            </button>
          ) : (
            <button
              onClick={connect}
              disabled={connectionStatus === "connecting"}
              className="flex items-center gap-1.5 px-3 py-1.5 bg-green-700 hover:bg-green-600
                         text-white rounded text-xs transition-colors disabled:opacity-50"
            >
              <Wifi size={12} />
              {connectionStatus === "connecting" ? "Connecting..." : "Connect"}
            </button>
          )}
        </div>

        {/* Server Health */}
        <div className="space-y-2">
          <div className="flex items-center justify-between">
            <span className="text-xs text-gray-400">Server Health</span>
            <button
              onClick={fetchHealth}
              disabled={loading}
              className="text-gray-400 hover:text-gray-200 transition-colors"
            >
              <RefreshCw size={12} className={loading ? "animate-spin" : ""} />
            </button>
          </div>
          {health ? (
            <div className="bg-gray-800 rounded p-3 text-xs space-y-1">
              <p className="text-green-400">Status: {health.status}</p>
              <p className="text-gray-400">Uptime: {Math.round(health.uptime_seconds)}s</p>
              <p className="text-gray-400">Sessions: {health.active_sessions}</p>
              <p className="text-gray-400">Model: {health.model}</p>
              <p className="text-gray-400 truncate">Ollama: {health.ollama_url}</p>
            </div>
          ) : (
            <p className="text-xs text-gray-500">
              {loading ? "Checking..." : "Server not reachable"}
            </p>
          )}
        </div>

        {/* Clear chat */}
        <button
          onClick={reset}
          className="flex items-center gap-1.5 w-full px-3 py-2 bg-gray-800 hover:bg-gray-700
                     text-gray-400 hover:text-gray-200 rounded text-xs transition-colors"
        >
          <Trash2 size={12} />
          Clear Chat & Reset
        </button>
      </div>
    </div>
  );
}
