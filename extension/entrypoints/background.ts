/**
 * Background Service Worker — WebSocket hub + message router.
 *
 * Everything must be inside defineBackground() for WXT to handle
 * service worker lifecycle correctly.
 *
 * Edge cases handled:
 * - Page navigation mid-task: re-inject content script, re-extract DOM after load
 * - Tab switching: track the task tab ID, always target it (not whatever is active)
 * - Content script disconnection: detect and re-inject gracefully
 * - Service worker idle timeout: keepalive ping every 20s
 *
 * Security: Only the opaque session_token is stored/transmitted.
 * Actual API keys are never in WebSocket frames or browser storage.
 */

export default defineBackground(() => {
  console.log('[Agentic Browser] Background service worker started');

  let ws: WebSocket | null = null;
  let sessionId: string | null = null;
  let serverUrl = 'ws://localhost:8000/ws';
  let sessionToken: string | null = null;
  let keepAliveInterval: ReturnType<typeof setInterval> | null = null;

  // Track which tab the current task is running on.
  // Once a goal is sent, we lock to that tab so tab-switching doesn't break actions.
  let taskTabId: number | null = null;

  // Load saved server URL and session token
  chrome.storage.local.get('serverUrl', (result: any) => {
    if (result.serverUrl) serverUrl = result.serverUrl;
  });
  chrome.storage.session.get('sessionToken', (result: any) => {
    if (result.sessionToken) sessionToken = result.sessionToken;
  });

  // Open side panel when extension icon is clicked
  chrome.sidePanel.setPanelBehavior({ openPanelOnActionClick: true });

  // --- Tab Navigation Listener ---
  // Detect when the task tab navigates (page reload, link click, etc.)
  // Re-inject content script and notify side panel of URL change.
  chrome.tabs.onUpdated.addListener((tabId: number, changeInfo: any, _tab: any) => {
    if (tabId !== taskTabId) return;

    if (changeInfo.status === 'complete') {
      console.log('[Agentic] Task tab navigation completed, re-injecting content script');
      ensureContentScript(tabId).then(() => {
        broadcastToSidePanel({
          type: 'server_message',
          data: {
            type: 'server_status',
            cognitive_status: 'executing',
            message: `Page navigated: ${changeInfo.url || 'loaded'}`,
            session_id: sessionId,
            timestamp: Date.now() / 1000,
          },
        });
      }).catch((err: any) => {
        console.warn('[Agentic] Failed to re-inject after navigation:', err.message);
      });
    }
  });

  // Detect when the task tab is closed
  chrome.tabs.onRemoved.addListener((tabId: number) => {
    if (tabId === taskTabId) {
      console.log('[Agentic] Task tab was closed');
      taskTabId = null;
      broadcastToSidePanel({
        type: 'server_message',
        data: {
          type: 'server_error',
          message: 'The tab this task was running on has been closed.',
          recoverable: false,
          timestamp: Date.now() / 1000,
        },
      });
      // Cancel the task on the server
      sendToServer({ type: 'client_cancel' });
    }
  });

  // --- WebSocket Connection ---

  function getWsUrl(): string {
    if (sessionToken) {
      const separator = serverUrl.includes('?') ? '&' : '?';
      return `${serverUrl}${separator}token=${encodeURIComponent(sessionToken)}`;
    }
    return serverUrl;
  }

  function connectWebSocket() {
    if (ws?.readyState === WebSocket.OPEN || ws?.readyState === WebSocket.CONNECTING) return;

    broadcastToSidePanel({ type: 'connection_status', status: 'connecting' });
    const wsUrl = getWsUrl();
    console.log('[Agentic] Connecting to', serverUrl, sessionToken ? '(with token)' : '(no token)');

    try {
      ws = new WebSocket(wsUrl);
    } catch (err: any) {
      console.error('[Agentic] WebSocket creation failed:', err);
      broadcastToSidePanel({ type: 'connection_status', status: 'error' });
      return;
    }

    ws.onopen = () => {
      console.log('[Agentic] WebSocket connected!');
      broadcastToSidePanel({ type: 'connection_status', status: 'connected' });
      startKeepAlive();
    };

    ws.onmessage = (event: any) => {
      try {
        const msg = JSON.parse(event.data);

        if (msg.type === 'server_status' && msg.session_id) {
          sessionId = msg.session_id;
        }

        // When task completes or fails, release the tab lock
        if (msg.type === 'server_done' || (msg.type === 'server_error' && !msg.recoverable)) {
          taskTabId = null;
        }

        broadcastToSidePanel({ type: 'server_message', data: msg });

        if (msg.type === 'server_action_request' && msg.execute) {
          handleActionExecution(msg);
        }
      } catch (err) {
        console.error('[Agentic] Parse error:', err);
      }
    };

    ws.onerror = (err: any) => {
      console.error('[Agentic] WebSocket error:', err);
      broadcastToSidePanel({ type: 'connection_status', status: 'error' });
    };

    ws.onclose = (event: any) => {
      console.log('[Agentic] WebSocket closed:', event.code, event.reason);
      ws = null;
      sessionId = null;
      taskTabId = null;
      stopKeepAlive();
      broadcastToSidePanel({ type: 'connection_status', status: 'disconnected' });
    };
  }

  function disconnectWebSocket() {
    stopKeepAlive();
    ws?.close();
    ws = null;
    sessionId = null;
    taskTabId = null;
  }

  // Chrome MV3 service workers are killed after ~30s of inactivity.
  // Periodic self-messaging keeps the worker alive while WebSocket is open.
  function startKeepAlive() {
    stopKeepAlive();
    keepAliveInterval = setInterval(() => {
      if (ws?.readyState === WebSocket.OPEN) {
        chrome.runtime.sendMessage({ type: '_keepalive' }).catch(() => {});
      } else {
        stopKeepAlive();
      }
    }, 20_000);
  }

  function stopKeepAlive() {
    if (keepAliveInterval) {
      clearInterval(keepAliveInterval);
      keepAliveInterval = null;
    }
  }

  function sendToServer(data: any) {
    if (ws?.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify(data));
    } else {
      console.warn('[Agentic] Cannot send — WebSocket not open. State:', ws?.readyState);
    }
  }

  // --- Side Panel Communication ---

  function broadcastToSidePanel(msg: any) {
    chrome.runtime.sendMessage(msg).catch(() => {});
  }

  // --- Content Script Helpers ---

  async function ensureContentScript(tabId: number): Promise<void> {
    try {
      await chrome.tabs.sendMessage(tabId, { type: 'ping' });
    } catch {
      console.log('[Agentic] Injecting content script into tab', tabId);
      await chrome.scripting.executeScript({
        target: { tabId },
        files: ['content-scripts/content.js'],
      });
      await new Promise(r => setTimeout(r, 300));
    }
  }

  /**
   * Wait for a tab to finish loading after a navigation action.
   * Returns once the tab's status is 'complete' or timeout.
   */
  function waitForTabLoad(tabId: number, timeoutMs = 10000): Promise<void> {
    return new Promise((resolve) => {
      const start = Date.now();

      const check = () => {
        chrome.tabs.get(tabId, (tab: any) => {
          if (chrome.runtime.lastError || !tab) {
            resolve(); // Tab gone
            return;
          }
          if (tab.status === 'complete' || Date.now() - start > timeoutMs) {
            resolve();
          } else {
            setTimeout(check, 200);
          }
        });
      };

      // Give the navigation a moment to start
      setTimeout(check, 300);
    });
  }

  /**
   * Get the tab ID to use for the current task.
   * If a task tab is locked, use it. Otherwise, use the active tab.
   */
  async function getTaskTabId(): Promise<number> {
    // If we have a locked task tab, verify it still exists
    if (taskTabId !== null) {
      try {
        const tab = await chrome.tabs.get(taskTabId);
        if (tab) return taskTabId;
      } catch {
        // Tab no longer exists
        taskTabId = null;
      }
    }

    // Fall back to active tab
    const tabs = await chrome.tabs.query({ active: true, currentWindow: true });
    const tab = tabs[0];
    if (!tab?.id) throw new Error('No active tab found');
    return tab.id;
  }

  async function extractDomFromTab(tabId: number): Promise<any> {
    await ensureContentScript(tabId);
    const result = await chrome.tabs.sendMessage(tabId, { type: 'extract_dom' });
    if (!result?.success) throw new Error('DOM extraction returned empty result');
    return result.data;
  }

  async function extractDomFromActiveTab(): Promise<any> {
    const tabId = await getTaskTabId();
    return extractDomFromTab(tabId);
  }

  // --- Action Execution ---

  // Actions that cause page navigation — need to wait for load before re-extracting DOM
  const NAVIGATION_ACTIONS = new Set([
    'navigate', 'go_back', 'go_forward', 'refresh',
  ]);

  // Actions handled directly by background (chrome.tabs API, not content script)
  const BG_ACTIONS = new Set(['new_tab', 'close_tab', 'switch_tab', 'take_screenshot', 'navigate']);

  async function handleBgAction(action: any, tabId: number): Promise<any> {
    const atype = action.action_type;

    if (atype === 'navigate') {
      const url = action.value || 'about:blank';
      console.log('[Agentic] Background handling navigate to:', url);
      await chrome.tabs.update(tabId, { url });
      await waitForTabLoad(tabId, 15000);
      await ensureContentScript(tabId);
      let dom: any = null;
      try { dom = await extractDomFromTab(tabId); } catch { /* page may block content script */ }
      return {
        result: { status: 'success', message: `Navigated to ${url}`, page_changed: true, new_url: url, execution_time_ms: 0 },
        dom,
      };
    }

    if (atype === 'new_tab') {
      const newTab = await chrome.tabs.create({ url: action.value || 'about:blank' });
      if (newTab.id) taskTabId = newTab.id;
      // Wait for load
      await waitForTabLoad(newTab.id!, 10000);
      await ensureContentScript(newTab.id!);
      const dom = await extractDomFromTab(newTab.id!);
      return {
        result: { status: 'success', message: `Opened new tab: ${action.value || 'blank'}`, page_changed: true, new_url: action.value, execution_time_ms: 0 },
        dom,
      };
    }

    if (atype === 'close_tab') {
      await chrome.tabs.remove(tabId);
      // Switch back to previous tab
      const tabs = await chrome.tabs.query({ currentWindow: true });
      if (tabs.length > 0 && tabs[0].id) {
        taskTabId = tabs[0].id;
        await chrome.tabs.update(taskTabId, { active: true });
        await ensureContentScript(taskTabId);
        const dom = await extractDomFromTab(taskTabId);
        return {
          result: { status: 'success', message: 'Tab closed', page_changed: true, execution_time_ms: 0 },
          dom,
        };
      }
      taskTabId = null;
      return {
        result: { status: 'success', message: 'Tab closed (no remaining tabs)', page_changed: true, execution_time_ms: 0 },
      };
    }

    if (atype === 'switch_tab') {
      const idx = parseInt(action.value || '0', 10);
      const tabs = await chrome.tabs.query({ currentWindow: true });
      if (idx >= 0 && idx < tabs.length && tabs[idx].id) {
        taskTabId = tabs[idx].id!;
        await chrome.tabs.update(taskTabId, { active: true });
        await waitForTabLoad(taskTabId, 5000);
        await ensureContentScript(taskTabId);
        const dom = await extractDomFromTab(taskTabId);
        return {
          result: { status: 'success', message: `Switched to tab ${idx}`, page_changed: true, execution_time_ms: 0 },
          dom,
        };
      }
      return {
        result: { status: 'failed', message: `Tab index ${idx} out of range (${tabs.length} tabs)`, page_changed: false, execution_time_ms: 0 },
      };
    }

    if (atype === 'take_screenshot') {
      try {
        const dataUrl = await chrome.tabs.captureVisibleTab(undefined, { format: 'png' });
        // For visual_check: send full image data for server-side vision analysis
        // For plain screenshot: truncate since we just need confirmation it was taken
        const isVisualCheck = action.value && action.value.startsWith('__VISUAL_CHECK__');
        const visualQuery = isVisualCheck ? action.value.replace('__VISUAL_CHECK__|', '') : '';
        return {
          result: {
            status: 'success',
            message: isVisualCheck ? 'Visual check screenshot captured' : 'Screenshot captured',
            page_changed: false,
            execution_time_ms: 0,
            extracted_data: isVisualCheck ? dataUrl : dataUrl.substring(0, 500) + '...(truncated)',
            description: visualQuery,  // Pass the query back so server can use it for vision model
          },
        };
      } catch (err: any) {
        return {
          result: { status: 'failed', message: `Screenshot failed: ${err.message}`, page_changed: false, execution_time_ms: 0 },
        };
      }
    }

    return null;
  }

  async function handleActionExecution(msg: any) {
    const action = msg.action;
    let tabId: number;

    try {
      tabId = await getTaskTabId();
    } catch {
      sendToServer({
        type: 'client_action_result',
        action_result: { status: 'failed', message: 'No active tab found' },
      });
      return;
    }

    // Handle background-only actions (tab management, screenshots)
    if (BG_ACTIONS.has(action.action_type)) {
      try {
        const bgResult = await handleBgAction(action, tabId);
        if (bgResult) {
          sendToServer({
            type: 'client_action_result',
            action_result: bgResult.result,
            ...(bgResult.dom ? { new_dom_snapshot: bgResult.dom } : {}),
          });
          return;
        }
      } catch (err: any) {
        sendToServer({
          type: 'client_action_result',
          action_result: { status: 'failed', message: `Background action error: ${err.message}`, execution_time_ms: 0 },
        });
        return;
      }
    }

    try {
      await ensureContentScript(tabId);

      const actionResult = await chrome.tabs.sendMessage(tabId, {
        type: 'execute_action',
        action: {
          action_type: action.action_type,
          element_id: action.element_id,
          value: action.value,
        },
      });

      // Non-mutating actions: skip DOM re-extraction (use cached context)
      // Same optimization as Playwright orchestrator — saves 200-500ms per action
      const NON_MUTATING = new Set([
        'scroll_down', 'scroll_up', 'extract_text', 'wait',
        'take_screenshot', 'get_console_logs', 'get_network_log',
        'wait_for_selector', 'wait_for_navigation',
      ]);

      const isNavAction = NAVIGATION_ACTIONS.has(action.action_type);
      const pageChanged = actionResult?.data?.page_changed;
      const skipDom = NON_MUTATING.has(action.action_type) && !pageChanged;

      let domData: any = null;

      if (!skipDom) {
        if (isNavAction || pageChanged) {
          console.log('[Agentic] Navigation detected, waiting for page load...');
          await waitForTabLoad(tabId, 10000);
          await ensureContentScript(tabId);
        } else {
          await new Promise(r => setTimeout(r, 800));
        }

        try {
          domData = await extractDomFromTab(tabId);
        } catch (domErr: any) {
          console.warn('[Agentic] DOM re-extraction failed after action:', domErr.message);
        }
      } else {
        console.log('[Agentic] Cached DOM (non-mutating action:', action.action_type, ')');
      }

      sendToServer({
        type: 'client_action_result',
        action_result: actionResult?.data || {
          status: 'success',
          message: 'Action executed',
          page_changed: isNavAction,
          execution_time_ms: 0,
        },
        ...(domData ? { new_dom_snapshot: domData } : {}),
      });
    } catch (err: any) {
      // Content script error — could be due to page navigation destroying it
      console.error('[Agentic] Action execution error:', err.message);

      // Try to recover: wait for page load and re-extract
      let domData: any = null;
      try {
        await waitForTabLoad(tabId, 5000);
        await ensureContentScript(tabId);
        domData = await extractDomFromTab(tabId);
      } catch {
        // Can't recover DOM — send error result
      }

      sendToServer({
        type: 'client_action_result',
        action_result: {
          status: domData ? 'success' : 'failed',
          message: domData
            ? 'Page navigated (content script reloaded)'
            : `Content script error: ${err.message}`,
          page_changed: !!domData,
          new_url: domData?.url,
          execution_time_ms: 0,
        },
        ...(domData ? { new_dom_snapshot: domData } : {}),
      });
    }
  }

  // --- Message Handler ---

  chrome.runtime.onMessage.addListener((message: any, _sender: any, sendResponse: any) => {
    const { type } = message;

    if (type === 'sp_connect') {
      serverUrl = message.serverUrl || serverUrl;
      chrome.storage.local.set({ serverUrl });
      if (message.sessionToken !== undefined) {
        sessionToken = message.sessionToken;
      }
      connectWebSocket();
      sendResponse({ success: true });
    }

    else if (type === 'sp_disconnect') {
      disconnectWebSocket();
      sendResponse({ success: true });
    }

    else if (type === 'sp_set_token') {
      sessionToken = message.sessionToken || null;
      sendResponse({ success: true });
    }

    else if (type === 'sp_send_goal') {
      // Lock the task to the current active tab
      chrome.tabs.query({ active: true, currentWindow: true }, (tabs: any[]) => {
        const tab = tabs[0];
        if (tab?.id) {
          taskTabId = tab.id;
          console.log('[Agentic] Task locked to tab', taskTabId, tab.url);
        }

        extractDomFromActiveTab().then(dom => {
          // Check if DOM extraction returned empty elements (e.g., Google's NTP looks like google.com but blocks scripts)
          const hasElements = dom?.elements?.length > 0;
          if (!hasElements && dom) {
            dom.page_text_summary = `Current page: ${dom.title || dom.url}. The page has no interactive elements accessible. You MUST use the navigate tool to go to the target website directly (e.g., navigate to https://youtube.com). Do NOT use ask_user — just navigate.`;
          }
          const payload: any = {
            type: 'client_goal',
            goal: message.goal,
            dom_snapshot: dom,
          };
          if (message.modelOverride) {
            payload.model_override = message.modelOverride;
          }
          sendToServer(payload);
          if (!hasElements) {
            broadcastToSidePanel({
              type: 'server_message',
              data: {
                type: 'server_status',
                cognitive_status: 'analyzing_goal',
                message: 'Page has no interactive elements — agent will navigate to the target site.',
                session_id: sessionId,
                timestamp: Date.now() / 1000,
              },
            });
          }
          sendResponse({ success: true });
        }).catch((err: any) => {
          console.warn('[Agentic] DOM extraction failed:', err.message);
          // Build a minimal page context so the agent knows to navigate
          const currentTab = tab;
          const isRestricted = !currentTab?.url || currentTab.url.startsWith('chrome://') || currentTab.url.startsWith('chrome-search://') || currentTab.url.startsWith('about:');
          const minimalDom = {
            url: currentTab?.url || 'about:blank',
            title: currentTab?.title || '',
            meta_description: '',
            page_text_summary: `Current page: ${currentTab?.title || currentTab?.url || 'blank'}. DOM extraction not available${isRestricted ? ' (browser internal page)' : ''}. You MUST use the navigate tool to go to the target website directly. Do NOT use ask_user — just navigate.`,
            elements: [],
            forms: [],
            viewport_width: 1280,
            viewport_height: 720,
            scroll_position: 0,
            has_more_content_below: false,
            timestamp: Date.now() / 1000,
          };
          const payload: any = {
            type: 'client_goal',
            goal: message.goal,
            dom_snapshot: minimalDom,
          };
          if (message.modelOverride) {
            payload.model_override = message.modelOverride;
          }
          sendToServer(payload);
          broadcastToSidePanel({
            type: 'server_message',
            data: {
              type: 'server_status',
              cognitive_status: 'analyzing_goal',
              message: isRestricted
                ? 'On a browser page — agent will navigate to the target site.'
                : `DOM extraction failed. Agent will navigate to the target site.`,
              session_id: sessionId,
              timestamp: Date.now() / 1000,
            },
          });
          sendResponse({ success: true, warning: 'Sent without full DOM' });
        });
      });
      return true; // Async
    }

    else if (type === 'sp_interrupt_response') {
      sendToServer({ type: 'client_user_response', values: message.values });
      sendResponse({ success: true });
    }

    else if (type === 'sp_cancel') {
      sendToServer({ type: 'client_cancel' });
      taskTabId = null;
      sendResponse({ success: true });
    }

    else if (type === 'sp_get_status') {
      sendResponse({
        connected: ws?.readyState === WebSocket.OPEN,
        sessionId,
        serverUrl,
        taskTabId,
      });
    }

    else if (type === 'dom_changed') {
      broadcastToSidePanel({ type: 'dom_changed' });
    }

    return false;
  });
});
