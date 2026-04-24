/**
 * Side Panel UI — The extension's main interface.
 *
 * Communicates with the background service worker which manages
 * the WebSocket connection and routes messages to/from content scripts.
 *
 * Key management: API keys are sent to the backend via REST (POST /api/keys).
 * Only the opaque session_token is stored in chrome.storage.session.
 * Actual keys never persist in the browser.
 */

// --- DOM Elements ---
const statusIndicator = document.getElementById('status-dot')!;
const statusText = document.getElementById('status-text')!;
const sessionIdEl = document.getElementById('session-id')!;
const liveStatus = document.getElementById('live-status')!;
const liveStatusText = document.getElementById('live-status-text')!;
const liveProgressFill = document.getElementById('live-progress-fill')!;
const messagesEl = document.getElementById('messages')!;
const welcomeEl = document.getElementById('welcome')!;
const interruptPanel = document.getElementById('interrupt-panel')!;
const interruptTitle = document.getElementById('interrupt-title')!;
const interruptContext = document.getElementById('interrupt-context')!;
const interruptFields = document.getElementById('interrupt-fields')!;
const goalInput = document.getElementById('goal-input') as HTMLInputElement;
const sendBtn = document.getElementById('send-btn')!;
const stopBtn = document.getElementById('stop-btn')!;
const settingsBtn = document.getElementById('settings-btn')!;
const connectBtn = document.getElementById('connect-btn')!;
const settingsPanel = document.getElementById('settings-panel')!;
const serverUrlInput = document.getElementById('server-url') as HTMLInputElement;
const saveUrlBtn = document.getElementById('save-url-btn')!;

// New settings elements
const openaiKeyInput = document.getElementById('openai-key-input') as HTMLInputElement;
const groqKeyInput = document.getElementById('groq-key-input') as HTMLInputElement;
const openrouterKeyInput = document.getElementById('openrouter-key-input') as HTMLInputElement;
const ollamaUrlInput = document.getElementById('ollama-url-input') as HTMLInputElement;
const saveKeysBtn = document.getElementById('save-keys-btn')!;
const providerSelect = document.getElementById('provider-select') as HTMLSelectElement;
const modelSelect = document.getElementById('model-select') as HTMLSelectElement;
const customModelInput = document.getElementById('custom-model-input') as HTMLInputElement;
const addModelBtn = document.getElementById('add-model-btn')!;
const openaiStatus = document.getElementById('openai-status')!;
const groqStatus = document.getElementById('groq-status')!;
const ollamaStatus = document.getElementById('ollama-status')!;

// --- State ---
let connected = false;
let isWorking = false;
let sessionToken: string | null = null;

// --- REST API Helpers ---

function getBaseUrl(): string {
  // Derive REST base URL from WebSocket URL
  const wsUrl = serverUrlInput.value.trim();
  return wsUrl.replace(/^ws/, 'http').replace(/\/ws\/?$/, '');
}

async function submitKeys(): Promise<void> {
  const baseUrl = getBaseUrl();
  const body: Record<string, string> = {};

  if (openaiKeyInput.value.trim()) body.openai_api_key = openaiKeyInput.value.trim();
  if (groqKeyInput.value.trim()) body.groq_api_key = groqKeyInput.value.trim();
  if (openrouterKeyInput.value.trim()) body.openrouter_api_key = openrouterKeyInput.value.trim();
  if (ollamaUrlInput.value.trim()) body.ollama_base_url = ollamaUrlInput.value.trim();
  if (providerSelect.value) body.preferred_provider = providerSelect.value;
  if (modelSelect.value) body.preferred_model = modelSelect.value;

  try {
    const resp = await fetch(`${baseUrl}/api/keys`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });

    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);

    const data = await resp.json();
    sessionToken = data.session_token;

    // Store token in session storage (cleared on browser close, never synced)
    chrome.storage.session.set({ sessionToken });

    // Clear key inputs immediately — keys should not linger in the DOM
    openaiKeyInput.value = '';
    groqKeyInput.value = '';

    // Update provider status dots
    updateProviderStatus(data.providers);

    addMessage('system', 'API keys saved securely.');

    // Notify background to use token for WebSocket
    chrome.runtime.sendMessage({ type: 'sp_set_token', sessionToken });

    // If already connected, reconnect with token
    if (connected) {
      chrome.runtime.sendMessage({ type: 'sp_disconnect' });
      setTimeout(() => {
        chrome.runtime.sendMessage({ type: 'sp_connect', serverUrl: serverUrlInput.value, sessionToken });
      }, 500);
    }
  } catch (err: any) {
    addMessage('error', `Failed to save keys: ${err.message}`);
  }
}

async function fetchProviders(): Promise<void> {
  const baseUrl = getBaseUrl();
  try {
    const url = sessionToken
      ? `${baseUrl}/api/providers?token=${encodeURIComponent(sessionToken)}`
      : `${baseUrl}/api/providers`;
    const resp = await fetch(url);
    if (!resp.ok) return;

    const data = await resp.json();
    const providers = data.providers || {};

    // Update status dots
    updateProviderStatus({
      openai: providers.openai?.available || false,
      groq: providers.groq?.available || false,
      ollama: providers.ollama?.available || false,
    });

    // Populate model dropdown based on selected provider
    populateModels(providers);
  } catch {
    // Backend not reachable — that's OK
  }
}

function updateProviderStatus(status: Record<string, boolean>): void {
  openaiStatus.className = `provider-dot ${status.openai ? 'active' : 'inactive'}`;
  groqStatus.className = `provider-dot ${status.groq ? 'active' : 'inactive'}`;
  ollamaStatus.className = `provider-dot ${status.ollama ? 'active' : 'inactive'}`;
}

function populateModels(providers: Record<string, any>): void {
  const selectedProvider = providerSelect.value;
  modelSelect.innerHTML = '<option value="">Default</option>';

  const addModels = (models: string[]) => {
    for (const model of models) {
      const opt = document.createElement('option');
      opt.value = model;
      opt.textContent = model;
      modelSelect.appendChild(opt);
    }
  };

  if (!selectedProvider || selectedProvider === 'openai') {
    if (providers.openai?.models) addModels(providers.openai.models);
  }
  if (!selectedProvider || selectedProvider === 'groq') {
    if (providers.groq?.models) addModels(providers.groq.models);
  }
  if (!selectedProvider || selectedProvider === 'ollama') {
    if (providers.ollama?.models) addModels(providers.ollama.models);
  }
}

// --- UI Helpers ---

function addMessage(role: 'user' | 'agent' | 'system' | 'error' | 'done', content: string, extra?: string) {
  welcomeEl.classList.add('hidden');
  const div = document.createElement('div');
  div.className = `msg ${role}`;

  const label = document.createElement('div');
  label.className = 'msg-label';
  label.textContent = role === 'user' ? 'You' : role === 'error' ? 'Error' : role === 'done' ? 'Done' : role === 'system' ? 'System' : 'Agent';
  div.appendChild(label);

  const text = document.createElement('div');
  text.textContent = content;
  div.appendChild(text);

  if (extra) {
    const extraEl = document.createElement('div');
    extraEl.innerHTML = extra;
    div.appendChild(extraEl);
  }

  messagesEl.appendChild(div);
  messagesEl.scrollTop = messagesEl.scrollHeight;
}

function addThinkingMessage(content: string, node: string) {
  welcomeEl.classList.add('hidden');
  const div = document.createElement('div');
  div.className = 'msg thinking';

  const header = document.createElement('div');
  header.className = 'thinking-header';
  header.innerHTML = `<span class="thinking-icon">&#x1f9e0;</span> <span class="thinking-label">Thinking</span><span class="thinking-node">${node}</span>`;
  header.style.cursor = 'pointer';
  div.appendChild(header);

  const body = document.createElement('div');
  body.className = 'thinking-body';
  body.textContent = content.substring(0, 500);
  body.style.display = 'none'; // Collapsed by default
  div.appendChild(body);

  // Toggle expand/collapse
  header.addEventListener('click', () => {
    const isHidden = body.style.display === 'none';
    body.style.display = isHidden ? 'block' : 'none';
    header.querySelector('.thinking-icon')!.textContent = isHidden ? '\u{1F4AD}' : '\u{1F9E0}';
  });

  messagesEl.appendChild(div);
  messagesEl.scrollTop = messagesEl.scrollHeight;
}

function addPlanMessage(steps: any[], version: number) {
  welcomeEl.classList.add('hidden');
  const div = document.createElement('div');
  div.className = 'msg agent';

  const label = document.createElement('div');
  label.className = 'msg-label';
  // S3.B: milestones are advisory scope, not an executable script. Label it
  // so the user does not expect the agent to march through steps in order.
  const countLabel = steps.length <= 1 ? 'Goal' : `Goals (${steps.length})`;
  label.textContent = `Agent — ${countLabel} · rev ${version}`;
  div.appendChild(label);

  for (let i = 0; i < steps.length && i < 6; i++) {
    const step = steps[i];
    const row = document.createElement('div');
    row.className = 'plan-step';
    // Use textContent — never innerHTML with agent/LLM-provided strings (XSS).
    const num = document.createElement('span');
    num.className = 'step-num';
    num.textContent = String(i + 1);
    const desc = document.createElement('span');
    desc.textContent = (step && (step.description || step)) || '';
    row.appendChild(num);
    row.appendChild(desc);
    div.appendChild(row);
  }

  messagesEl.appendChild(div);
  messagesEl.scrollTop = messagesEl.scrollHeight;
}

function addActionMessage(action: any) {
  const conf = action.confidence || 0;
  const confClass = conf >= 0.8 ? 'high-conf' : conf >= 0.5 ? 'med-conf' : 'low-conf';
  const extra = `
    <span class="action-badge ${confClass}">${action.action_type} — ${Math.round(conf * 100)}%</span>
    element [${action.element_id ?? 'N/A'}]
    ${action.description ? `<div style="font-size:11px;color:#888;margin-top:4px">${action.description}</div>` : ''}
  `;
  addMessage('agent', '', extra);
}

function addEvalMessage(data: any) {
  const progress = data.progress_percentage || 0;
  const extra = `
    <div class="progress-bar"><div class="progress-fill" style="width:${progress}%"></div></div>
    <div style="font-size:11px;color:#888;margin-top:4px">${data.summary || ''}</div>
  `;
  addMessage('agent', data.action_succeeded ? 'Action succeeded' : 'Action failed', extra);
}

function addDoneMessage(success: boolean, summary: string, actions: number, rawData?: any) {
  welcomeEl.classList.add('hidden');
  const div = document.createElement('div');
  div.className = `msg done-result ${success ? 'success' : 'failed'}`;

  // Header
  const header = document.createElement('div');
  header.className = 'done-header';
  header.innerHTML = `
    <span class="done-icon">${success ? '&#x2705;' : '&#x274C;'}</span>
    <span class="done-title">${success ? 'Task Completed' : 'Task Failed'}</span>
    ${actions > 0 ? `<span class="done-actions">${actions} actions</span>` : ''}
  `;
  div.appendChild(header);

  // Summary content
  const body = document.createElement('div');
  body.className = 'done-body';
  // Split by newlines for better formatting
  const lines = summary.split('\n').filter((l: string) => l.trim());
  for (const line of lines) {
    const p = document.createElement('p');
    p.textContent = line.trim();
    if (line.trim().startsWith('Findings:')) {
      p.className = 'done-findings-label';
    }
    body.appendChild(p);
  }
  div.appendChild(body);

  // Export download buttons (if structured data is available)
  if (rawData?.export_available && rawData.export_id) {
    const exportBar = document.createElement('div');
    exportBar.className = 'export-bar';
    const label = document.createElement('span');
    label.className = 'export-label';
    label.textContent = `Download (${rawData.export_items || '?'} items):`;
    exportBar.appendChild(label);

    const formats = [
      { label: 'JSON', key: 'json' },
      { label: 'CSV', key: 'csv' },
      { label: 'Excel', key: 'xlsx' },
      { label: 'PDF', key: 'pdf' },
    ];
    for (const fmt of formats) {
      const btn = document.createElement('button');
      btn.className = 'export-btn';
      btn.textContent = fmt.label;
      btn.addEventListener('click', async () => {
        const baseUrl = getBaseUrl();
        const url = `${baseUrl}/api/export/${rawData.export_id}?format=${fmt.key}`;
        try {
          btn.disabled = true;
          btn.textContent = '...';
          const resp = await fetch(url);
          if (!resp.ok) {
            throw new Error(`HTTP ${resp.status}`);
          }
          const blob = await resp.blob();
          const disposition = resp.headers.get('Content-Disposition') || '';
          const match = disposition.match(/filename="?(.+?)"?$/);
          const filename = match ? match[1] : `export.${fmt.key}`;
          const a = document.createElement('a');
          a.href = URL.createObjectURL(blob);
          a.download = filename;
          document.body.appendChild(a);
          a.click();
          document.body.removeChild(a);
          URL.revokeObjectURL(a.href);
          btn.textContent = fmt.label;
        } catch (e: any) {
          console.error('Export download failed:', e);
          btn.textContent = 'Failed';
          setTimeout(() => { btn.textContent = fmt.label; }, 2000);
        } finally {
          btn.disabled = false;
        }
      });
      exportBar.appendChild(btn);
    }
    div.appendChild(exportBar);
  }

  messagesEl.appendChild(div);
  messagesEl.scrollTop = messagesEl.scrollHeight;
}

// --- Streaming Bubble ---
let streamingBubble: HTMLElement | null = null;
let streamingBody: HTMLElement | null = null;

function startStreamingBubble() {
  // If there's already a streaming bubble, finalize it first
  if (streamingBubble) finalizeStreamingBubble();

  welcomeEl.classList.add('hidden');
  const div = document.createElement('div');
  div.className = 'msg streaming';

  const body = document.createElement('div');
  body.className = 'streaming-body';
  body.innerHTML = '<span class="streaming-cursor"></span>';
  div.appendChild(body);

  messagesEl.appendChild(div);
  messagesEl.scrollTop = messagesEl.scrollHeight;

  streamingBubble = div;
  streamingBody = body;
}

function appendToStreamingBubble(token: string) {
  if (!streamingBody) startStreamingBubble();

  // Filter out <think> tags — show thinking content but strip the tags
  const cleaned = token.replace(/<\/?think>/g, '');
  if (!cleaned) return;

  // Remove cursor, append text, re-add cursor
  const cursor = streamingBody!.querySelector('.streaming-cursor');
  if (cursor) cursor.remove();

  streamingBody!.insertAdjacentText('beforeend', cleaned);

  // Re-add cursor at the end
  const newCursor = document.createElement('span');
  newCursor.className = 'streaming-cursor';
  streamingBody!.appendChild(newCursor);

  // Auto-scroll
  messagesEl.scrollTop = messagesEl.scrollHeight;

  // Truncate if too long (keep last 500 chars visible)
  const text = streamingBody!.textContent || '';
  if (text.length > 600) {
    const cursor2 = streamingBody!.querySelector('.streaming-cursor');
    if (cursor2) cursor2.remove();
    streamingBody!.textContent = '...' + text.slice(-500);
    const nc = document.createElement('span');
    nc.className = 'streaming-cursor';
    streamingBody!.appendChild(nc);
  }
}

function finalizeStreamingBubble() {
  if (!streamingBubble) return;

  // Remove cursor
  const cursor = streamingBubble.querySelector('.streaming-cursor');
  if (cursor) cursor.remove();

  // Remove streaming class (stops animation)
  streamingBubble.classList.remove('streaming');
  streamingBubble.classList.add('agent');

  // If the bubble is empty or very short, remove it
  const text = (streamingBody?.textContent || '').trim();
  if (text.length < 5) {
    streamingBubble.remove();
  }

  streamingBubble = null;
  streamingBody = null;
}

function setConnectionStatus(status: string) {
  statusIndicator.className = status;
  statusText.textContent = status.charAt(0).toUpperCase() + status.slice(1);
  connected = status === 'connected';

  goalInput.disabled = !connected || isWorking;
  goalInput.placeholder = connected ? (isWorking ? 'Agent is working...' : 'What would you like me to do?') : 'Connect to get started...';
  sendBtn.disabled = !connected || isWorking;
  connectBtn.textContent = connected ? 'Disconnect' : 'Connect';

  // Fetch providers when connected
  if (connected) fetchProviders();
}

function setWorking(working: boolean) {
  isWorking = working;
  goalInput.disabled = !connected || working;
  goalInput.placeholder = working ? 'Agent is working...' : 'What would you like me to do?';
  sendBtn.classList.toggle('hidden', working);
  stopBtn.classList.toggle('hidden', !working);
  sendBtn.disabled = !connected || working;
  liveStatus.classList.toggle('hidden', !working);
  if (!working) {
    liveProgressFill.style.width = '0%';
  }
}

function updateLiveStatus(text: string, progress?: number) {
  liveStatusText.textContent = text;
  if (progress !== undefined) {
    liveProgressFill.style.width = `${Math.min(100, Math.max(0, progress))}%`;
  }
}

function showInterrupt(data: any) {
  interruptPanel.classList.remove('hidden');
  interruptTitle.textContent = data.title || 'Agent needs input';
  interruptContext.textContent = data.context || '';
  interruptFields.innerHTML = '';

  const fields = data.fields || [];
  for (const field of fields) {
    if (field.field_type === 'confirm') {
      const yesBtn = document.createElement('button');
      yesBtn.className = 'interrupt-btn confirm';
      yesBtn.textContent = field.options?.[0] || 'Confirm';
      yesBtn.onclick = () => {
        chrome.runtime.sendMessage({ type: 'sp_interrupt_response', values: { [field.field_id]: true } });
        interruptPanel.classList.add('hidden');
      };

      const noBtn = document.createElement('button');
      noBtn.className = 'interrupt-btn deny';
      noBtn.textContent = field.options?.[1] || 'Deny';
      noBtn.onclick = () => {
        chrome.runtime.sendMessage({ type: 'sp_interrupt_response', values: { [field.field_id]: false } });
        interruptPanel.classList.add('hidden');
      };

      interruptFields.appendChild(yesBtn);
      interruptFields.appendChild(noBtn);
    } else if (field.field_type === 'text') {
      const input = document.createElement('input');
      input.className = 'interrupt-input';
      input.placeholder = field.label || 'Your response...';

      const submitBtn = document.createElement('button');
      submitBtn.className = 'interrupt-btn confirm';
      submitBtn.textContent = 'Send';
      submitBtn.onclick = () => {
        chrome.runtime.sendMessage({ type: 'sp_interrupt_response', values: { [field.field_id]: input.value } });
        interruptPanel.classList.add('hidden');
      };

      input.addEventListener('keydown', (e) => {
        if (e.key === 'Enter') submitBtn.click();
      });

      interruptFields.appendChild(input);
      interruptFields.appendChild(submitBtn);
    }
  }
}

// --- Message Listener (from background) ---

chrome.runtime.onMessage.addListener((message) => {
  const { type, data } = message;

  if (type === 'connection_status') {
    setConnectionStatus(message.status);
    // Show reconnection or failure messages from the background script
    if (message.message && (message.status === 'reconnecting' || message.status === 'disconnected')) {
      statusText.textContent = message.message;
    }
  }

  else if (type === 'server_message' && data) {
    const msgType = data.type;

    if (msgType === 'server_status') {
      if (data.session_id) sessionIdEl.textContent = data.session_id;
      if (data.cognitive_status && data.cognitive_status !== 'connected') {
        const statusMap: Record<string, string> = {
          'analyzing_goal': 'Analyzing goal...',
          'creating_plan': 'Creating plan...',
          'self_critiquing': 'Reviewing plan...',
          'reasoning': 'Reasoning...',
          'deciding': 'Selecting action...',
          'executing': 'Executing action...',
          'observing': 'Observing changes...',
          'evaluating': 'Evaluating result...',
          're_planning': 'Re-planning...',
          'retrying': 'Retrying...',
          'completed': 'Done',
          'failed': 'Failed',
        };
        const displayStatus = statusMap[data.cognitive_status] || data.cognitive_status.replace(/_/g, ' ');
        updateLiveStatus(displayStatus);
        if (data.cognitive_status !== 'idle' && data.cognitive_status !== 'completed' && data.cognitive_status !== 'failed') {
          setWorking(true);
        }
      }
    }

    else if (msgType === 'server_stream_start') {
      startStreamingBubble();
    }

    else if (msgType === 'server_token') {
      appendToStreamingBubble(data.token || '');
    }

    else if (msgType === 'server_stream_end') {
      finalizeStreamingBubble();
    }

    else if (msgType === 'server_thinking') {
      addThinkingMessage(data.content || '', data.node || '');
    }

    else if (msgType === 'server_reasoning') {
      addMessage('agent', data.content?.substring(0, 300) || '');
    }

    else if (msgType === 'server_plan') {
      addPlanMessage(data.steps || [], data.plan_version || 1);
    }

    else if (msgType === 'server_action_request' && !data.execute) {
      addActionMessage(data.action || {});
    }

    else if (msgType === 'server_evaluation') {
      addEvalMessage(data);
      const progress = data.progress_percentage || 0;
      updateLiveStatus(data.action_succeeded ? 'Action succeeded' : 'Action failed', progress);
    }

    else if (msgType === 'server_interrupt') {
      showInterrupt(data);
    }

    else if (msgType === 'server_done') {
      setWorking(false);
      addDoneMessage(data.success, data.summary || 'Task complete', data.total_actions || 0, data);
    }

    else if (msgType === 'server_warning') {
      addMessage('system', `⚠️ ${data.message || 'Warning from server'}`);
    }

    else if (msgType === 'server_error') {
      addMessage('error', data.message || 'Unknown error');
      if (!data.recoverable) setWorking(false);
    }
  }
});

// --- Event Listeners ---

sendBtn.addEventListener('click', () => {
  const goal = goalInput.value.trim();
  if (!goal || !connected) return;

  addMessage('user', goal);
  goalInput.value = '';
  setWorking(true);

  // Include selected model as override
  const modelOverride = modelSelect.value || customModelInput.value.trim() || undefined;
  // Include session memory setting
  const memoryKInput = document.getElementById('session-memory-k') as HTMLInputElement;
  const sessionMemoryK = memoryKInput ? parseInt(memoryKInput.value) || 6 : 6;
  chrome.runtime.sendMessage({ type: 'sp_send_goal', goal, modelOverride, sessionMemoryK });
});

goalInput.addEventListener('keydown', (e) => {
  if (e.key === 'Enter') sendBtn.click();
});

stopBtn.addEventListener('click', () => {
  chrome.runtime.sendMessage({ type: 'sp_cancel' });
  setWorking(false);
  addMessage('system', 'Task cancelled');
});

connectBtn.addEventListener('click', () => {
  if (connected) {
    chrome.runtime.sendMessage({ type: 'sp_disconnect' });
  } else {
    chrome.runtime.sendMessage({ type: 'sp_connect', serverUrl: serverUrlInput.value, sessionToken });
  }
});

settingsBtn.addEventListener('click', () => {
  const wasHidden = settingsPanel.classList.contains('hidden');
  settingsPanel.classList.toggle('hidden');
  // Refresh providers when opening settings
  if (wasHidden) fetchProviders();
});

saveUrlBtn.addEventListener('click', () => {
  const url = serverUrlInput.value.trim();
  if (url) {
    chrome.runtime.sendMessage({ type: 'sp_connect', serverUrl: url, sessionToken });
    settingsPanel.classList.add('hidden');
  }
});

saveKeysBtn.addEventListener('click', () => {
  submitKeys();
});

providerSelect.addEventListener('change', () => {
  fetchProviders();
});

addModelBtn.addEventListener('click', () => {
  const model = customModelInput.value.trim();
  if (model) {
    const opt = document.createElement('option');
    opt.value = model;
    opt.textContent = model;
    modelSelect.appendChild(opt);
    modelSelect.value = model;
    customModelInput.value = '';
  }
});

// --- Init: Check current status ---
chrome.runtime.sendMessage({ type: 'sp_get_status' }, (response) => {
  if (response) {
    setConnectionStatus(response.connected ? 'connected' : 'disconnected');
    if (response.sessionId) sessionIdEl.textContent = response.sessionId;
    if (response.serverUrl) serverUrlInput.value = response.serverUrl;
  }
});

// Restore session token from session storage
chrome.storage.session.get('sessionToken', (result) => {
  if (result.sessionToken) {
    sessionToken = result.sessionToken;
  }
});
