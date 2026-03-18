"use client";

import { useCallback } from "react";
import { useAgentStore } from "@/stores/agent-store";
import type { DOMSnapshot, ServerMessage } from "@/lib/types";
import { uniqueId } from "@/lib/utils";

// Module-level singleton — shared across all components
let ws: WebSocket | null = null;

function getWs(): WebSocket | null {
  return ws?.readyState === WebSocket.OPEN ? ws : null;
}

export function useWebSocket() {
  const {
    serverUrl,
    connectionStatus,
    setConnectionStatus,
    setSessionId,
    handleServerMessage,
    addMessage,
  } = useAgentStore();

  const connect = useCallback(() => {
    if (ws?.readyState === WebSocket.OPEN || ws?.readyState === WebSocket.CONNECTING) return;

    setConnectionStatus("connecting");

    const socket = new WebSocket(serverUrl);
    ws = socket;

    socket.onopen = () => {
      setConnectionStatus("connected");
    };

    socket.onmessage = (event) => {
      try {
        const msg: ServerMessage = JSON.parse(event.data);
        useAgentStore.getState().handleServerMessage(msg);
      } catch {
        console.error("Failed to parse server message:", event.data);
      }
    };

    socket.onerror = () => {
      setConnectionStatus("error");
    };

    socket.onclose = () => {
      ws = null;
      useAgentStore.setState({
        connectionStatus: "disconnected",
        sessionId: null,
      });
    };
  }, [serverUrl, setConnectionStatus, setSessionId, handleServerMessage]);

  const disconnect = useCallback(() => {
    ws?.close();
    ws = null;
    setConnectionStatus("disconnected");
    setSessionId(null);
  }, [setConnectionStatus, setSessionId]);

  const sendGoal = useCallback(
    (goal: string, domSnapshot?: DOMSnapshot) => {
      const socket = getWs();
      if (!socket) return;

      useAgentStore.setState({
        taskDone: false,
        taskSuccess: null,
        currentPlan: null,
        currentAction: null,
        pendingInterrupt: null,
        cognitiveStatus: "analyzing_goal",
      });

      addMessage({
        id: uniqueId(),
        role: "user",
        content: goal,
        timestamp: Date.now() / 1000,
      });

      const msg: Record<string, unknown> = {
        type: "client_goal",
        goal,
      };
      if (domSnapshot) {
        msg.dom_snapshot = domSnapshot;
      }
      socket.send(JSON.stringify(msg));
    },
    [addMessage]
  );

  const sendInterruptResponse = useCallback(
    (values: Record<string, unknown>) => {
      const socket = getWs();
      if (!socket) return;

      useAgentStore.setState({ pendingInterrupt: null });

      socket.send(
        JSON.stringify({
          type: "client_user_response",
          values,
        })
      );
    },
    []
  );

  const sendActionResult = useCallback(
    (result: {
      status: string;
      message: string;
      page_changed?: boolean;
      new_url?: string;
      execution_time_ms?: number;
    }, newDom?: DOMSnapshot) => {
      const socket = getWs();
      if (!socket) return;

      socket.send(
        JSON.stringify({
          type: "client_action_result",
          action_result: result,
          new_dom_snapshot: newDom,
        })
      );
    },
    []
  );

  const cancelTask = useCallback(() => {
    const socket = getWs();
    if (!socket) return;

    socket.send(JSON.stringify({ type: "client_cancel" }));
    useAgentStore.setState({
      cognitiveStatus: "idle",
      pendingInterrupt: null,
      currentAction: null,
    });
  }, []);

  return {
    connect,
    disconnect,
    sendGoal,
    sendInterruptResponse,
    sendActionResult,
    cancelTask,
    isConnected: connectionStatus === "connected",
  };
}
