/**
 * Content Script — Runs in the context of every web page.
 *
 * Responsibilities:
 * 1. DOM Extractor: Extract interactive elements into PageContext schema
 * 2. Action Executor: Execute agent actions (click, type, scroll, etc.)
 * 3. State Observer: Watch for DOM changes via MutationObserver
 *
 * Hardened for complex sites (Google, YouTube, Facebook):
 * - Shadow DOM traversal for Web Components
 * - Priority-based element cap (interactive > viewport > informational)
 * - data-testid / data-qa attribute collection
 * - Nested element deduplication
 * - Table extraction support
 * - Key combo support
 *
 * Communication: content script ↔ background service worker via chrome.runtime messages
 */

// --- Types matching backend schema ---

interface BoundingBox {
  x: number; y: number; width: number; height: number;
}

interface DOMElement {
  element_id: number;
  element_type: string;
  tag_name: string;
  text: string;
  attributes: Record<string, string>;
  is_visible: boolean;
  is_enabled: boolean;
  is_focused: boolean;
  bounding_box: BoundingBox | null;
  parent_context: string;
  children_count: number;
  css_selector: string;
}

interface PageContext {
  url: string;
  title: string;
  meta_description: string;
  page_text_summary: string;
  elements: DOMElement[];
  forms: { name: string; action: string; method: string; field_ids: number[] }[];
  viewport_width: number;
  viewport_height: number;
  scroll_position: number;
  has_more_content_below: boolean;
  timestamp: number;
}

// --- Element ID ↔ DOM node mapping ---
const elementMap = new Map<number, Element>();

// --- Tag to element type mapping ---
const TAG_TYPE_MAP: Record<string, string> = {
  a: 'link', button: 'button', textarea: 'textarea', select: 'select',
  h1: 'heading', h2: 'heading', h3: 'heading', h4: 'heading', h5: 'heading', h6: 'heading',
  p: 'paragraph', li: 'list_item', img: 'image', nav: 'nav_item', dialog: 'dialog',
  table: 'other', video: 'other', audio: 'other',
};

const INPUT_TYPE_MAP: Record<string, string> = {
  text: 'text_input', email: 'text_input', password: 'text_input', search: 'text_input',
  tel: 'text_input', url: 'text_input', number: 'text_input',
  checkbox: 'checkbox', radio: 'radio', file: 'file_input', range: 'slider',
  submit: 'button', reset: 'button', button: 'button',
};

const ROLE_TYPE_MAP: Record<string, string> = {
  button: 'button', link: 'link', tab: 'tab', menuitem: 'menu_item',
  checkbox: 'checkbox', radio: 'radio', switch: 'toggle', slider: 'slider',
  textbox: 'text_input', searchbox: 'text_input', combobox: 'select',
  option: 'list_item', listbox: 'select',
};

// Attributes to collect from each element
const COLLECT_ATTRS = [
  'type', 'name', 'placeholder', 'aria-label', 'aria-labelledby',
  'href', 'role', 'title', 'alt', 'value', 'action', 'method',
  'data-testid', 'data-qa', 'data-cy', 'data-test', 'data-id',
  'aria-expanded', 'aria-selected', 'aria-checked', 'aria-disabled',
  'contenteditable', 'tabindex', 'target',
];

// Selectors for interactive elements (highest priority)
const INTERACTIVE_SELECTORS = [
  'a[href]', 'button', 'input', 'textarea', 'select',
  '[role="button"]', '[role="link"]', '[role="tab"]',
  '[role="menuitem"]', '[role="checkbox"]', '[role="radio"]',
  '[role="switch"]', '[role="slider"]', '[role="textbox"]',
  '[role="searchbox"]', '[role="combobox"]', '[role="option"]',
  '[onclick]', '[contenteditable="true"]',
  'summary', 'details',
];

// Selectors for informational elements (lower priority)
const INFO_SELECTORS = [
  'h1', 'h2', 'h3', 'h4', 'h5', 'h6',
  'p', 'li', 'img[alt]', 'label', 'table',
  '[role="heading"]', '[role="alert"]', '[role="status"]',
];

function resolveElementType(el: Element): string {
  const role = el.getAttribute('role') || '';
  if (ROLE_TYPE_MAP[role]) return ROLE_TYPE_MAP[role];

  const tag = el.tagName.toLowerCase();
  if (tag === 'input') {
    const type = (el.getAttribute('type') || 'text').toLowerCase();
    return INPUT_TYPE_MAP[type] || 'text_input';
  }
  if (TAG_TYPE_MAP[tag]) return TAG_TYPE_MAP[tag];
  if (el.getAttribute('onclick') || el.getAttribute('contenteditable') === 'true') return 'button';
  if (el.getAttribute('tabindex')) return 'button';
  return 'other';
}

/**
 * Check if an element is a hashed/auto-generated class name.
 * These are unreliable for selectors (e.g., "css-1a2b3c", "sc-fKgJPI").
 */
function isHashedClass(cls: string): boolean {
  // Matches patterns like: css-1abc, sc-fKgJPI, _1abc2, emotion-0
  return /^(css-|sc-|_[a-z0-9]{4,}|emotion-|e[0-9]{4,}|jsx-|styled-)/.test(cls)
    || /^[a-zA-Z]{1,3}[A-Z][a-zA-Z0-9]{4,}$/.test(cls)  // camelCase hash
    || /^[a-f0-9]{6,}$/.test(cls);  // pure hex hash
}

/**
 * Build a robust CSS selector for an element.
 * Prefers: #id > [data-testid] > tag.stable-class > nth-child path
 */
function buildSelector(el: Element): string {
  // 1. ID (if stable — not auto-generated)
  const id = (el as HTMLElement).id;
  if (id && !isHashedClass(id) && !id.startsWith(':')) {
    return `#${CSS.escape(id)}`;
  }

  // 2. data-testid or data-qa
  for (const attr of ['data-testid', 'data-qa', 'data-cy', 'data-test']) {
    const val = el.getAttribute(attr);
    if (val) return `[${attr}="${CSS.escape(val)}"]`;
  }

  // 3. Tag + stable classes
  const tag = el.tagName.toLowerCase();
  if (el.className && typeof el.className === 'string') {
    const stableClasses = el.className.trim().split(/\s+/)
      .filter(c => c && !isHashedClass(c))
      .slice(0, 2);
    if (stableClasses.length > 0) {
      return `${tag}.${stableClasses.join('.')}`;
    }
  }

  // 4. Tag + name attribute
  const name = el.getAttribute('name');
  if (name) return `${tag}[name="${CSS.escape(name)}"]`;

  // 5. Tag + aria-label
  const ariaLabel = el.getAttribute('aria-label');
  if (ariaLabel) return `${tag}[aria-label="${CSS.escape(ariaLabel)}"]`;

  // 6. Just tag name
  return tag;
}

/**
 * Check if this element is nested inside another interactive element
 * that we've already captured. If so, skip it to avoid wasting slots.
 */
function isNestedInteractive(el: Element, seen: Set<Element>): boolean {
  let parent = el.parentElement;
  while (parent && parent !== document.body) {
    if (seen.has(parent)) {
      const parentTag = parent.tagName.toLowerCase();
      if (['a', 'button', 'label'].includes(parentTag)
        || parent.getAttribute('role') === 'button'
        || parent.getAttribute('role') === 'link'
        || parent.getAttribute('onclick')) {
        return true;
      }
    }
    parent = parent.parentElement;
  }
  return false;
}

/**
 * Collect elements from a root, including shadow DOM traversal.
 */
function collectElements(root: Document | ShadowRoot | Element, selectors: string[]): Element[] {
  const elements: Element[] = [];
  for (const selector of selectors) {
    try {
      root.querySelectorAll(selector).forEach(el => elements.push(el));
    } catch { /* invalid selector in this context */ }
  }

  // Traverse shadow DOMs
  if (root instanceof Document || root instanceof Element) {
    const allEls = root instanceof Document
      ? root.querySelectorAll('*')
      : root.querySelectorAll('*');
    allEls.forEach(el => {
      if (el.shadowRoot) {
        elements.push(...collectElements(el.shadowRoot, selectors));
      }
    });
  }

  return elements;
}

// --- DOM Extractor ---

function extractPageContext(maxInteractive = 250, maxInfo = 50): PageContext {
  elementMap.clear();

  const seen = new Set<Element>();
  const elements: DOMElement[] = [];
  let id = 0;

  const viewportHeight = window.innerHeight;
  const scrollY = window.scrollY;

  function processElement(el: Element, priority: 'interactive' | 'info'): boolean {
    if (seen.has(el)) return false;
    seen.add(el);

    // Skip nested interactive elements (e.g., <span> inside <button> inside <a>)
    if (priority === 'interactive' && isNestedInteractive(el, seen)) return false;

    const rect = el.getBoundingClientRect();
    const style = window.getComputedStyle(el);
    const isVisible = (
      style.display !== 'none' &&
      style.visibility !== 'hidden' &&
      style.opacity !== '0' &&
      rect.width > 0 && rect.height > 0
    );

    // Skip invisible elements (except hidden inputs which may still matter)
    const tag = el.tagName.toLowerCase();
    if (!isVisible && !(tag === 'input' && el.getAttribute('type') === 'hidden')) return false;

    id++;

    // Collect attributes
    const attrs: Record<string, string> = {};
    for (const name of COLLECT_ATTRS) {
      const val = el.getAttribute(name);
      if (val) attrs[name] = val;
    }

    // Text content (trimmed, capped)
    let text = (el.textContent || '').trim();
    if (text.length > 200) text = text.substring(0, 200);

    // Parent context
    let parentContext = '';
    const form = el.closest('form');
    if (form) {
      parentContext = `inside form: ${form.getAttribute('name') || form.getAttribute('id') || 'unnamed'}`;
    } else if (el.closest('nav, [role="navigation"]')) {
      parentContext = 'inside nav bar';
    } else if (el.closest('dialog, [role="dialog"], [role="alertdialog"]')) {
      parentContext = 'inside dialog';
    } else if (el.closest('header')) {
      parentContext = 'inside header';
    } else if (el.closest('footer')) {
      parentContext = 'inside footer';
    } else if (el.closest('[role="menu"], [role="menubar"]')) {
      parentContext = 'inside menu';
    }

    // Is element in the current viewport?
    const inViewport = rect.top < viewportHeight + scrollY && rect.bottom > scrollY;

    elementMap.set(id, el);

    elements.push({
      element_id: id,
      element_type: resolveElementType(el),
      tag_name: tag,
      text,
      attributes: attrs,
      is_visible: isVisible,
      is_enabled: !(el as HTMLInputElement).disabled,
      is_focused: document.activeElement === el,
      bounding_box: isVisible ? {
        x: Math.round(rect.x),
        y: Math.round(rect.y),
        width: Math.round(rect.width),
        height: Math.round(rect.height),
      } : null,
      parent_context: parentContext,
      children_count: el.querySelectorAll('a, button, input, textarea, select').length,
      css_selector: buildSelector(el),
    });

    return true;
  }

  // Phase 1: Collect interactive elements (highest priority)
  const interactiveEls = collectElements(document, INTERACTIVE_SELECTORS);

  // Sort: viewport elements first, then by DOM order
  const viewportInteractive: Element[] = [];
  const offscreenInteractive: Element[] = [];

  for (const el of interactiveEls) {
    const rect = el.getBoundingClientRect();
    if (rect.top < viewportHeight && rect.bottom > 0) {
      viewportInteractive.push(el);
    } else {
      offscreenInteractive.push(el);
    }
  }

  // Process viewport interactive first
  for (const el of viewportInteractive) {
    if (id >= maxInteractive) break;
    processElement(el, 'interactive');
  }

  // Then offscreen interactive
  for (const el of offscreenInteractive) {
    if (id >= maxInteractive) break;
    processElement(el, 'interactive');
  }

  // Phase 2: Collect informational elements (lower priority)
  const infoEls = collectElements(document, INFO_SELECTORS);
  let infoCount = 0;
  for (const el of infoEls) {
    if (infoCount >= maxInfo) break;
    if (processElement(el, 'info')) infoCount++;
  }

  // Forms
  const forms = Array.from(document.querySelectorAll('form')).map(form => {
    const fieldIds: number[] = [];
    elementMap.forEach((el, eid) => {
      if (form.contains(el)) fieldIds.push(eid);
    });
    return {
      name: form.getAttribute('name') || form.getAttribute('id') || 'unnamed',
      action: form.getAttribute('action') || '',
      method: (form.getAttribute('method') || 'GET').toUpperCase(),
      field_ids: fieldIds,
    };
  });

  // Scroll info
  const scrollHeight = document.documentElement.scrollHeight;
  const clientHeight = document.documentElement.clientHeight;
  const scrollTop = window.scrollY;
  const scrollPos = scrollHeight > clientHeight ? scrollTop / (scrollHeight - clientHeight) : 0;

  // Page text summary
  const bodyText = (document.body.innerText || '').trim();

  return {
    url: window.location.href,
    title: document.title,
    meta_description: document.querySelector('meta[name="description"]')?.getAttribute('content') || '',
    page_text_summary: bodyText.substring(0, 500),
    elements,
    forms,
    viewport_width: window.innerWidth,
    viewport_height: window.innerHeight,
    scroll_position: Math.min(1, Math.max(0, scrollPos)),
    has_more_content_below: (scrollTop + clientHeight) < (scrollHeight - 50),
    timestamp: Date.now() / 1000,
  };
}

// --- Console & Network Monitoring ---
// These arrays are populated by monkey-patched console/fetch/XHR in main().

const MAX_LOG_ENTRIES = 50;
const consoleLogs: { level: string; message: string; timestamp: number }[] = [];
const networkLog: { method: string; url: string; status: number; type: string; timestamp: number }[] = [];

// Keep reference to original console for internal logging
let _origLog: (...args: any[]) => void = console.log.bind(console);

/**
 * Set up monitoring hooks. Must be called inside main() (browser context only).
 */
function setupMonitoring() {
  // --- Console interception ---
  const origConsole = {
    log: console.log.bind(console),
    warn: console.warn.bind(console),
    error: console.error.bind(console),
    info: console.info.bind(console),
  };
  _origLog = origConsole.log;

  function captureConsole(level: string, origFn: (...args: any[]) => void) {
    return function (...args: any[]) {
      const message = args.map((a: any) => {
        try { return typeof a === 'string' ? a : JSON.stringify(a); }
        catch { return String(a); }
      }).join(' ').substring(0, 500);
      consoleLogs.push({ level, message, timestamp: Date.now() });
      if (consoleLogs.length > MAX_LOG_ENTRIES) consoleLogs.shift();
      origFn(...args);
    };
  }

  console.log = captureConsole('log', origConsole.log) as any;
  console.warn = captureConsole('warn', origConsole.warn) as any;
  console.error = captureConsole('error', origConsole.error) as any;
  console.info = captureConsole('info', origConsole.info) as any;

  // --- Fetch interception ---
  const origFetch = window.fetch.bind(window);
  (window as any).fetch = async function (input: any, init?: any) {
    const url = typeof input === 'string' ? input : input?.url || String(input);
    const method = init?.method || 'GET';
    const startTime = Date.now();
    try {
      const response = await origFetch(input, init);
      networkLog.push({ method, url: url.substring(0, 300), status: response.status, type: 'fetch', timestamp: startTime });
      if (networkLog.length > MAX_LOG_ENTRIES) networkLog.shift();
      return response;
    } catch (err) {
      networkLog.push({ method, url: url.substring(0, 300), status: 0, type: 'fetch-error', timestamp: startTime });
      if (networkLog.length > MAX_LOG_ENTRIES) networkLog.shift();
      throw err;
    }
  };

  // --- XMLHttpRequest interception ---
  if (typeof XMLHttpRequest !== 'undefined') {
    const origOpen = XMLHttpRequest.prototype.open;
    XMLHttpRequest.prototype.open = function (method: string, url: string | URL, ...rest: any[]) {
      (this as any).__agenticMethod = method;
      (this as any).__agenticUrl = String(url).substring(0, 300);
      (this as any).__agenticStart = Date.now();
      return (origOpen as any).call(this, method, url, ...rest);
    };
    const origSend = XMLHttpRequest.prototype.send;
    XMLHttpRequest.prototype.send = function (...args: any[]) {
      this.addEventListener('loadend', function () {
        networkLog.push({
          method: (this as any).__agenticMethod || 'GET',
          url: (this as any).__agenticUrl || '',
          status: this.status,
          type: 'xhr',
          timestamp: (this as any).__agenticStart || Date.now(),
        });
        if (networkLog.length > MAX_LOG_ENTRIES) networkLog.shift();
      });
      return (origSend as any).call(this, ...args);
    };
  }

  // --- Dialog interception ---
  window.alert = function (message?: any) {
    consoleLogs.push({ level: 'dialog', message: `[alert] ${message}`, timestamp: Date.now() });
    _origLog('[Agentic] Intercepted alert:', message);
  };

  window.confirm = function (message?: string): boolean {
    consoleLogs.push({ level: 'dialog', message: `[confirm] ${message}`, timestamp: Date.now() });
    _origLog('[Agentic] Intercepted confirm:', message);
    return true;
  };

  window.prompt = function (message?: string, defaultValue?: string): string | null {
    consoleLogs.push({ level: 'dialog', message: `[prompt] ${message} (default: ${defaultValue})`, timestamp: Date.now() });
    _origLog('[Agentic] Intercepted prompt:', message);
    return defaultValue || '';
  };
}

// --- Action Executor ---

interface ActionRequest {
  action_type: string;
  element_id: number | null;
  value?: string;
}

interface ActionResult {
  status: string;
  message: string;
  page_changed: boolean;
  new_url?: string;
  execution_time_ms: number;
  extracted_data?: string;
}

/**
 * Type text into an element using multiple strategies.
 * Strategy 1: execCommand (works on most contenteditable and inputs)
 * Strategy 2: Simulate individual keydown/keypress/keyup events
 * Strategy 3: Direct .value set + input event (fallback)
 * After typing, verifies the text was actually entered.
 */
async function typeIntoElement(
  el: HTMLElement,
  text: string,
  clearFirst: boolean,
): Promise<{ success: boolean; error: string }> {
  // Focus the element first
  el.focus();
  el.click();
  await new Promise(r => setTimeout(r, 50));

  const inputEl = el as HTMLInputElement;
  const isInput = 'value' in el && (el.tagName === 'INPUT' || el.tagName === 'TEXTAREA');
  const isContentEditable = el.isContentEditable;

  // Clear existing content if requested
  if (clearFirst) {
    if (isInput) {
      inputEl.value = '';
      el.dispatchEvent(new Event('input', { bubbles: true }));
    } else if (isContentEditable) {
      el.textContent = '';
    }
    // Also try select-all + delete
    document.execCommand('selectAll', false);
    document.execCommand('delete', false);
    await new Promise(r => setTimeout(r, 30));
  }

  // Strategy 1: execCommand('insertText') — works on most modern inputs including Google
  let worked = false;
  if (document.queryCommandSupported?.('insertText') !== false) {
    worked = document.execCommand('insertText', false, text);
  }

  // Strategy 2: If execCommand didn't work, simulate individual key events
  if (!worked || (isInput && inputEl.value !== text && !inputEl.value.includes(text))) {
    if (isInput) {
      // Direct set as last resort, but with proper event simulation
      const nativeInputValueSetter = Object.getOwnPropertyDescriptor(
        window.HTMLInputElement.prototype, 'value'
      )?.set || Object.getOwnPropertyDescriptor(
        window.HTMLTextAreaElement.prototype, 'value'
      )?.set;

      if (nativeInputValueSetter) {
        nativeInputValueSetter.call(el, clearFirst ? text : inputEl.value + text);
      } else {
        inputEl.value = clearFirst ? text : inputEl.value + text;
      }

      // Dispatch React-compatible events
      el.dispatchEvent(new Event('input', { bubbles: true }));
      el.dispatchEvent(new Event('change', { bubbles: true }));

      // Also fire individual key events for each character (helps React/Angular)
      for (const char of text) {
        el.dispatchEvent(new KeyboardEvent('keydown', { key: char, bubbles: true }));
        el.dispatchEvent(new KeyboardEvent('keypress', { key: char, bubbles: true }));
        el.dispatchEvent(new KeyboardEvent('keyup', { key: char, bubbles: true }));
      }
    } else if (isContentEditable) {
      el.textContent = (clearFirst ? '' : (el.textContent || '')) + text;
      el.dispatchEvent(new Event('input', { bubbles: true }));
    }
  }

  // Small delay for frameworks to process
  await new Promise(r => setTimeout(r, 100));

  // Verify the text was entered
  if (isInput) {
    const currentValue = inputEl.value;
    if (!currentValue.includes(text.substring(0, Math.min(10, text.length)))) {
      return { success: false, error: `Text verification failed: input value is "${currentValue.substring(0, 50)}" but expected to contain "${text.substring(0, 30)}"` };
    }
  }

  return { success: true, error: '' };
}

/**
 * Simulate pressing Enter — tries multiple approaches.
 */
async function simulateEnter(el: HTMLElement): Promise<void> {
  // Dispatch keyboard events
  el.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', code: 'Enter', keyCode: 13, which: 13, bubbles: true }));
  el.dispatchEvent(new KeyboardEvent('keypress', { key: 'Enter', code: 'Enter', keyCode: 13, which: 13, bubbles: true }));
  el.dispatchEvent(new KeyboardEvent('keyup', { key: 'Enter', code: 'Enter', keyCode: 13, which: 13, bubbles: true }));

  // Also try submitting parent form
  const form = el.closest('form');
  if (form) {
    try { form.requestSubmit(); } catch { form.submit(); }
  }
}

async function executeAction(action: ActionRequest): Promise<ActionResult> {
  const start = performance.now();
  const oldUrl = window.location.href;

  try {
    const result = await dispatchAction(action);
    result.execution_time_ms = performance.now() - start;
    result.page_changed = window.location.href !== oldUrl;
    if (result.page_changed) result.new_url = window.location.href;
    return result;
  } catch (err: any) {
    return {
      status: 'failed',
      message: `Action failed: ${err.message}`,
      page_changed: false,
      execution_time_ms: performance.now() - start,
    };
  }
}

async function dispatchAction(action: ActionRequest): Promise<ActionResult> {
  const { action_type, element_id, value } = action;

  // Get target element
  let el: Element | undefined;
  if (element_id != null) {
    el = elementMap.get(element_id);
    if (!el) {
      return { status: 'element_not_found', message: `Element ${element_id} not found in current DOM map`, page_changed: false, execution_time_ms: 0 };
    }
  }

  switch (action_type) {
    case 'click':
      (el as HTMLElement)?.click();
      return { status: 'success', message: `Clicked element ${element_id}`, page_changed: false, execution_time_ms: 0 };

    case 'type_text':
    case 'fill': {
      const shouldSubmit = value?.endsWith('|SUBMIT');
      const actualText = shouldSubmit ? value!.slice(0, -7) : (value || '');
      if (!el) return { status: 'element_not_found', message: 'No element to type into', page_changed: false, execution_time_ms: 0 };

      const typed = await typeIntoElement(el as HTMLElement, actualText, false);
      if (!typed.success) {
        return { status: 'failed', message: typed.error, page_changed: false, execution_time_ms: 0 };
      }

      if (shouldSubmit) {
        await new Promise(r => setTimeout(r, 150));
        await simulateEnter(el as HTMLElement);
      }
      return { status: 'success', message: `Typed '${actualText}'${shouldSubmit ? ' and submitted' : ''}`, page_changed: shouldSubmit || false, execution_time_ms: 0 };
    }

    case 'clear_and_type': {
      const shouldSubmitClear = value?.endsWith('|SUBMIT');
      const actualTextClear = shouldSubmitClear ? value!.slice(0, -7) : (value || '');
      if (!el) return { status: 'element_not_found', message: 'No element to type into', page_changed: false, execution_time_ms: 0 };

      const typed = await typeIntoElement(el as HTMLElement, actualTextClear, true);
      if (!typed.success) {
        return { status: 'failed', message: typed.error, page_changed: false, execution_time_ms: 0 };
      }

      if (shouldSubmitClear) {
        await new Promise(r => setTimeout(r, 150));
        await simulateEnter(el as HTMLElement);
      }
      return { status: 'success', message: `Cleared and typed '${actualTextClear}'${shouldSubmitClear ? ' and submitted' : ''}`, page_changed: shouldSubmitClear || false, execution_time_ms: 0 };
    }

    case 'select_option':
      if (el && el.tagName === 'SELECT') {
        (el as HTMLSelectElement).value = value || '';
        el.dispatchEvent(new Event('change', { bubbles: true }));
      }
      return { status: 'success', message: `Selected option '${value}'`, page_changed: false, execution_time_ms: 0 };

    case 'check':
      if (el && 'checked' in el) {
        (el as HTMLInputElement).checked = true;
        el.dispatchEvent(new Event('change', { bubbles: true }));
      }
      return { status: 'success', message: `Checked element ${element_id}`, page_changed: false, execution_time_ms: 0 };

    case 'uncheck':
      if (el && 'checked' in el) {
        (el as HTMLInputElement).checked = false;
        el.dispatchEvent(new Event('change', { bubbles: true }));
      }
      return { status: 'success', message: `Unchecked element ${element_id}`, page_changed: false, execution_time_ms: 0 };

    case 'hover':
      (el as HTMLElement)?.dispatchEvent(new MouseEvent('mouseover', { bubbles: true }));
      (el as HTMLElement)?.dispatchEvent(new MouseEvent('mouseenter', { bubbles: true }));
      return { status: 'success', message: `Hovered element ${element_id}`, page_changed: false, execution_time_ms: 0 };

    case 'scroll_down':
      window.scrollBy(0, window.innerHeight * 0.8);
      return { status: 'success', message: 'Scrolled down', page_changed: false, execution_time_ms: 0 };

    case 'scroll_up':
      window.scrollBy(0, -window.innerHeight * 0.8);
      return { status: 'success', message: 'Scrolled up', page_changed: false, execution_time_ms: 0 };

    case 'scroll_to_element':
      (el as HTMLElement)?.scrollIntoView({ behavior: 'smooth', block: 'center' });
      return { status: 'success', message: `Scrolled to element ${element_id}`, page_changed: false, execution_time_ms: 0 };

    case 'navigate':
      window.location.href = value || '';
      return { status: 'success', message: `Navigating to ${value}`, page_changed: true, execution_time_ms: 0 };

    case 'go_back':
      history.back();
      return { status: 'success', message: 'Navigated back', page_changed: true, execution_time_ms: 0 };

    case 'go_forward':
      history.forward();
      return { status: 'success', message: 'Navigated forward', page_changed: true, execution_time_ms: 0 };

    case 'refresh':
      location.reload();
      return { status: 'success', message: 'Page refreshed', page_changed: true, execution_time_ms: 0 };

    case 'press_key':
      document.activeElement?.dispatchEvent(new KeyboardEvent('keydown', { key: value || 'Enter', bubbles: true }));
      document.activeElement?.dispatchEvent(new KeyboardEvent('keyup', { key: value || 'Enter', bubbles: true }));
      return { status: 'success', message: `Pressed key: ${value}`, page_changed: false, execution_time_ms: 0 };

    case 'key_combo': {
      // Parse combo like "Ctrl+Shift+Enter"
      const parts = (value || '').split('+').map(k => k.trim());
      const key = parts.pop() || 'Enter';
      const opts: KeyboardEventInit = {
        key,
        bubbles: true,
        ctrlKey: parts.some(p => p.toLowerCase() === 'ctrl'),
        shiftKey: parts.some(p => p.toLowerCase() === 'shift'),
        altKey: parts.some(p => p.toLowerCase() === 'alt'),
        metaKey: parts.some(p => p.toLowerCase() === 'meta' || p.toLowerCase() === 'cmd'),
      };
      const target = el ? (el as HTMLElement) : document.activeElement || document.body;
      target.dispatchEvent(new KeyboardEvent('keydown', opts));
      target.dispatchEvent(new KeyboardEvent('keyup', opts));
      return { status: 'success', message: `Pressed key combo: ${value}`, page_changed: false, execution_time_ms: 0 };
    }

    case 'extract_text': {
      const text = el ? (el as HTMLElement).innerText : document.body.innerText;
      return { status: 'success', message: 'Extracted text', page_changed: false, execution_time_ms: 0, extracted_data: text?.substring(0, 2000) };
    }

    case 'extract_table': {
      if (!el || el.tagName !== 'TABLE') {
        // Try to find a table inside the element
        const table = el?.querySelector('table');
        if (!table) {
          return { status: 'failed', message: 'No table found at element', page_changed: false, execution_time_ms: 0 };
        }
        el = table;
      }
      const rows: string[][] = [];
      (el as HTMLTableElement).querySelectorAll('tr').forEach(tr => {
        const cells: string[] = [];
        tr.querySelectorAll('th, td').forEach(cell => {
          cells.push((cell as HTMLElement).innerText.trim());
        });
        if (cells.length > 0) rows.push(cells);
      });
      // Format as TSV for easy reading
      const tsv = rows.map(r => r.join('\t')).join('\n');
      return { status: 'success', message: `Extracted table: ${rows.length} rows`, page_changed: false, execution_time_ms: 0, extracted_data: tsv.substring(0, 3000) };
    }

    case 'take_screenshot':
      // Screenshots must be handled by the background script (chrome.tabs.captureVisibleTab)
      return { status: 'success', message: 'Screenshot requested (handled by background)', page_changed: false, execution_time_ms: 0 };

    // Tab management — relayed to the background script
    case 'new_tab':
    case 'close_tab':
    case 'switch_tab':
      return { status: 'success', message: `${action_type} relayed to background`, page_changed: false, execution_time_ms: 0 };

    // --- Console & Network monitoring ---
    case 'get_console_logs': {
      const logs = consoleLogs.slice(-30).map(l => `[${l.level}] ${l.message}`).join('\n');
      return { status: 'success', message: `${consoleLogs.length} console entries`, page_changed: false, execution_time_ms: 0, extracted_data: logs || '(no console output captured)' };
    }

    case 'get_network_log': {
      const nets = networkLog.slice(-30).map(n => `${n.method} ${n.url} → ${n.status} (${n.type})`).join('\n');
      return { status: 'success', message: `${networkLog.length} network entries`, page_changed: false, execution_time_ms: 0, extracted_data: nets || '(no network requests captured)' };
    }

    // --- JavaScript evaluation ---
    case 'evaluate_js': {
      try {
        const fn = new Function(`return (${value})`);
        let result = fn();
        if (result instanceof Promise) result = await result;
        const output = typeof result === 'string' ? result : JSON.stringify(result, null, 2);
        return { status: 'success', message: 'JS evaluated', page_changed: false, execution_time_ms: 0, extracted_data: (output || 'undefined').substring(0, 3000) };
      } catch (err: any) {
        return { status: 'failed', message: `JS error: ${err.message}`, page_changed: false, execution_time_ms: 0 };
      }
    }

    // --- Dialog handling ---
    case 'handle_dialog': {
      // Dialogs are already auto-intercepted by our overrides.
      // This action lets the agent review what dialogs appeared.
      const dialogLogs = consoleLogs.filter(l => l.level === 'dialog').slice(-5);
      const summary = dialogLogs.length > 0
        ? dialogLogs.map(l => l.message).join('\n')
        : 'No recent dialogs intercepted';
      return { status: 'success', message: summary, page_changed: false, execution_time_ms: 0, extracted_data: summary };
    }

    // --- File upload ---
    case 'upload_file': {
      if (!el || el.tagName !== 'INPUT' || (el as HTMLInputElement).type !== 'file') {
        return { status: 'failed', message: 'Element is not a file input', page_changed: false, execution_time_ms: 0 };
      }
      // File upload from content script is limited — we can trigger the file dialog
      // but cannot set files programmatically due to browser security.
      // Signal to background to handle via chrome.debugger if needed.
      (el as HTMLElement).click();
      return { status: 'success', message: 'File input clicked (file dialog opened)', page_changed: false, execution_time_ms: 0 };
    }

    // --- Drag and drop ---
    case 'drag': {
      if (!el) return { status: 'element_not_found', message: 'Source element not found', page_changed: false, execution_time_ms: 0 };
      const targetId = parseInt(value || '0', 10);
      const targetEl = elementMap.get(targetId);
      if (!targetEl) return { status: 'element_not_found', message: `Target element ${targetId} not found`, page_changed: false, execution_time_ms: 0 };

      const srcRect = el.getBoundingClientRect();
      const tgtRect = targetEl.getBoundingClientRect();
      const dataTransfer = new DataTransfer();

      el.dispatchEvent(new DragEvent('dragstart', { bubbles: true, clientX: srcRect.x + srcRect.width / 2, clientY: srcRect.y + srcRect.height / 2, dataTransfer }));
      targetEl.dispatchEvent(new DragEvent('dragenter', { bubbles: true, clientX: tgtRect.x + tgtRect.width / 2, clientY: tgtRect.y + tgtRect.height / 2, dataTransfer }));
      targetEl.dispatchEvent(new DragEvent('dragover', { bubbles: true, clientX: tgtRect.x + tgtRect.width / 2, clientY: tgtRect.y + tgtRect.height / 2, dataTransfer }));
      targetEl.dispatchEvent(new DragEvent('drop', { bubbles: true, clientX: tgtRect.x + tgtRect.width / 2, clientY: tgtRect.y + tgtRect.height / 2, dataTransfer }));
      el.dispatchEvent(new DragEvent('dragend', { bubbles: true, dataTransfer }));

      return { status: 'success', message: `Dragged element ${element_id} to element ${targetId}`, page_changed: false, execution_time_ms: 0 };
    }

    // --- Smart waiting ---
    case 'wait_for_selector': {
      const [selector, timeoutStr] = (value || '|10').split('|');
      const timeout = Math.min(parseFloat(timeoutStr || '10') * 1000, 30000);

      const found = await new Promise<boolean>((resolve) => {
        // Check immediately
        if (document.querySelector(selector)) { resolve(true); return; }

        const obs = new MutationObserver(() => {
          if (document.querySelector(selector)) {
            obs.disconnect();
            resolve(true);
          }
        });
        obs.observe(document.body, { childList: true, subtree: true, attributes: true });

        setTimeout(() => { obs.disconnect(); resolve(false); }, timeout);
      });

      return {
        status: found ? 'success' : 'timeout',
        message: found ? `Selector "${selector}" found` : `Selector "${selector}" not found within ${timeoutStr}s`,
        page_changed: false,
        execution_time_ms: 0,
      };
    }

    case 'wait_for_navigation': {
      const navTimeout = Math.min(parseFloat(value || '10') * 1000, 30000);
      const startUrl = window.location.href;

      const navigated = await new Promise<boolean>((resolve) => {
        const check = setInterval(() => {
          if (window.location.href !== startUrl) {
            clearInterval(check);
            resolve(true);
          }
        }, 200);
        setTimeout(() => { clearInterval(check); resolve(false); }, navTimeout);
      });

      return {
        status: navigated ? 'success' : 'timeout',
        message: navigated ? `Navigated to ${window.location.href}` : `No navigation within ${value}s`,
        page_changed: navigated,
        new_url: navigated ? window.location.href : undefined,
        execution_time_ms: 0,
      };
    }

    case 'wait':
      await new Promise(r => setTimeout(r, Math.min(parseFloat(value || '1') * 1000, 10000)));
      return { status: 'success', message: `Waited ${value}s`, page_changed: false, execution_time_ms: 0 };

    case 'done':
      return { status: 'success', message: 'Task marked done', page_changed: false, execution_time_ms: 0 };

    default:
      return { status: 'failed', message: `Unknown action: ${action_type}`, page_changed: false, execution_time_ms: 0 };
  }
}

// --- State Observer (MutationObserver) ---

let observer: MutationObserver | null = null;
let domChangeTimer: ReturnType<typeof setTimeout> | null = null;

function startObserver() {
  if (observer) return;

  observer = new MutationObserver(() => {
    // Debounce: notify after 500ms of no changes
    if (domChangeTimer) clearTimeout(domChangeTimer);
    domChangeTimer = setTimeout(() => {
      try {
        if (!chrome.runtime?.id) {
          stopObserver();
          return;
        }
        chrome.runtime.sendMessage({ type: 'dom_changed' }).catch(() => {
          stopObserver();
        });
      } catch {
        stopObserver();
      }
    }, 500);
  });

  observer.observe(document.body, {
    childList: true,
    subtree: true,
    attributes: true,
    attributeFilter: ['class', 'style', 'hidden', 'disabled', 'value', 'aria-expanded', 'aria-selected'],
  });
}

function stopObserver() {
  observer?.disconnect();
  observer = null;
}

// --- Message Handler ---

export default defineContentScript({
  matches: ['<all_urls>'],

  main() {
    console.log('[Agentic Browser] Content script loaded on', window.location.href);

    // Set up console/network/dialog monitoring hooks
    setupMonitoring();

    // Start observing DOM changes
    startObserver();

    // Listen for messages from background service worker
    chrome.runtime.onMessage.addListener((message: any, _sender: any, sendResponse: any) => {
      const { type } = message;

      if (type === 'extract_dom') {
        const context = extractPageContext(
          message.maxInteractive || 250,
          message.maxInfo || 50,
        );
        sendResponse({ success: true, data: context });
      }

      else if (type === 'execute_action') {
        // Async action execution
        executeAction(message.action).then(result => {
          sendResponse({ success: true, data: result });
        }).catch(err => {
          sendResponse({ success: false, error: err.message });
        });
        return true; // Keep channel open for async response
      }

      else if (type === 'ping') {
        sendResponse({ success: true, alive: true });
      }

      else if (type === 'stop_observer') {
        stopObserver();
        sendResponse({ success: true });
      }

      else if (type === 'start_observer') {
        startObserver();
        sendResponse({ success: true });
      }

      return false;
    });
  },
});
