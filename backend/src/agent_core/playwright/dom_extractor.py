"""Live DOM extractor — Converts a Playwright page into PageContext.

This is the Playwright equivalent of the browser extension's content script.
It walks the live DOM, finds all interactive/notable elements, assigns numeric IDs,
and returns a PageContext matching the exact Pydantic schema the agent expects.

The agent never sees raw HTML — only the structured PageContext.
"""

import time
from playwright.async_api import Page

from agent_core.schemas.dom import (
    PageContext,
    DOMElement,
    ElementType,
    BoundingBox,
)

# Map HTML tags + attributes to our ElementType enum
_TAG_TO_TYPE: dict[str, ElementType] = {
    "a": ElementType.LINK,
    "button": ElementType.BUTTON,
    "textarea": ElementType.TEXTAREA,
    "select": ElementType.SELECT,
    "h1": ElementType.HEADING,
    "h2": ElementType.HEADING,
    "h3": ElementType.HEADING,
    "h4": ElementType.HEADING,
    "h5": ElementType.HEADING,
    "h6": ElementType.HEADING,
    "p": ElementType.PARAGRAPH,
    "li": ElementType.LIST_ITEM,
    "img": ElementType.IMAGE,
    "nav": ElementType.NAV_ITEM,
    "dialog": ElementType.DIALOG,
}

_INPUT_TYPE_MAP: dict[str, ElementType] = {
    "text": ElementType.TEXT_INPUT,
    "email": ElementType.TEXT_INPUT,
    "password": ElementType.TEXT_INPUT,
    "search": ElementType.TEXT_INPUT,
    "tel": ElementType.TEXT_INPUT,
    "url": ElementType.TEXT_INPUT,
    "number": ElementType.TEXT_INPUT,
    "checkbox": ElementType.CHECKBOX,
    "radio": ElementType.RADIO,
    "file": ElementType.FILE_INPUT,
    "range": ElementType.SLIDER,
    "submit": ElementType.BUTTON,
    "reset": ElementType.BUTTON,
    "button": ElementType.BUTTON,
}

# JavaScript to extract all elements from the live page
_EXTRACT_JS = """
() => {
    // Priority-based element collection:
    // P0 (highest): inputs, textareas, selects, buttons — always included
    // P1: viewport links, role-based interactive elements
    // P2: off-screen links, informational elements
    // This ensures search boxes, form fields, and buttons are NEVER cut off

    const seen = new Set();

    // Skip non-interactive containers (p, li, div wrapping interactive children)
    function isNestedContainer(el) {
        const interactiveTags = new Set(['a', 'button', 'input', 'textarea', 'select']);
        const tag = el.tagName.toLowerCase();
        if (!interactiveTags.has(tag) && !el.getAttribute('role') && !el.getAttribute('onclick')) {
            const ic = el.querySelectorAll('a, button, input, textarea, select, [role="button"], [role="link"]');
            if (ic.length > 0) return true;
        }
        return false;
    }

    function collectElements(selectors) {
        const result = [];
        for (const selector of selectors) {
            for (const el of document.querySelectorAll(selector)) {
                if (seen.has(el)) continue;
                if (isNestedContainer(el)) continue;
                seen.add(el);
                result.push(el);
            }
        }
        return result;
    }

    const viewH = window.innerHeight;
    const scrollY = window.scrollY;

    // P0: Form elements — ALWAYS included (inputs, textareas, selects, buttons)
    const p0 = collectElements([
        'input:not([type="hidden"])', 'textarea', 'select',
        'button', '[role="button"]', '[role="checkbox"]',
        '[role="radio"]', '[role="switch"]', '[role="slider"]',
    ]);

    // P1: Viewport interactive elements (links, tabs, menu items in view)
    const p1Raw = collectElements([
        'a[href]', '[role="link"]', '[role="tab"]', '[role="menuitem"]',
        '[onclick]', '[tabindex]',
    ]);
    const p1 = [];
    const p1Offscreen = [];
    for (const el of p1Raw) {
        const rect = el.getBoundingClientRect();
        const inView = rect.top < viewH + 100 && rect.bottom > -100;
        if (inView) p1.push(el);
        else p1Offscreen.push(el);
    }

    // P2: Informational elements (headings, paragraphs, images)
    const p2 = collectElements([
        'h1', 'h2', 'h3', 'h4', 'h5', 'h6',
        'p', 'li', 'img[alt]', 'nav', 'dialog', 'label',
    ]);

    // Merge in priority order
    const allElements = [...p0, ...p1, ...p1Offscreen, ...p2];
    const elements = [];

    for (const el of allElements) {

            const rect = el.getBoundingClientRect();
            const style = window.getComputedStyle(el);
            const isVisible = (
                style.display !== 'none' &&
                style.visibility !== 'hidden' &&
                style.opacity !== '0' &&
                rect.width > 0 && rect.height > 0
            );

            // Get relevant attributes
            const attrs = {};
            for (const name of ['type', 'name', 'placeholder', 'aria-label', 'href',
                                 'role', 'title', 'alt', 'value', 'action', 'method', 'src']) {
                const val = el.getAttribute(name);
                if (val) attrs[name] = val;
            }

            // Get visible text (limit length)
            let text = (el.innerText || el.textContent || '').trim();
            if (text.length > 200) text = text.substring(0, 200);

            // Determine parent context
            let parentContext = '';
            const form = el.closest('form');
            if (form) {
                const formName = form.getAttribute('name') || form.getAttribute('id') || 'unnamed';
                parentContext = 'inside form: ' + formName;
            } else {
                const nav = el.closest('nav');
                if (nav) parentContext = 'inside nav bar';
                const dialog = el.closest('dialog, [role="dialog"]');
                if (dialog) parentContext = 'inside dialog';
            }

            // Build a robust CSS selector — prioritize unique attributes
            let cssSelector = '';
            const tag = el.tagName.toLowerCase();

            if (el.id && !/^[0-9]/.test(el.id) && !/[:.]/.test(el.id)) {
                // ID-based selector (most reliable)
                cssSelector = '#' + CSS.escape(el.id);
            } else if (el.getAttribute('data-testid')) {
                cssSelector = tag + '[data-testid="' + el.getAttribute('data-testid') + '"]';
            } else if (el.getAttribute('data-qa')) {
                cssSelector = tag + '[data-qa="' + el.getAttribute('data-qa') + '"]';
            } else if (attrs.name) {
                cssSelector = tag + '[name="' + attrs.name + '"]';
            } else if (attrs['aria-label']) {
                cssSelector = tag + '[aria-label="' + attrs['aria-label'].replace(/"/g, '\\\\"') + '"]';
            } else if (attrs.role && text && text.length < 60) {
                // role + text combo for buttons/links
                cssSelector = tag + '[role="' + attrs.role + '"]';
            } else if (tag === 'a' && attrs.href) {
                // For links, use href (truncated)
                const href = attrs.href.length > 80 ? attrs.href.substring(0, 80) : attrs.href;
                cssSelector = 'a[href="' + href.replace(/"/g, '\\\\"') + '"]';
            } else if (attrs.type && (tag === 'input' || tag === 'button')) {
                cssSelector = tag + '[type="' + attrs.type + '"]';
                if (attrs.placeholder) {
                    cssSelector += '[placeholder="' + attrs.placeholder.replace(/"/g, '\\\\"') + '"]';
                }
            } else {
                // Fallback: tag + stable classes (filter auto-generated)
                cssSelector = tag;
                if (el.className && typeof el.className === 'string') {
                    const stableClasses = el.className.trim().split(/\\s+/)
                        .filter(c => c.length > 1 && c.length < 30
                            && !/^(css-|sc-|emotion-|styled-|_|js-|\\d)/.test(c))
                        .slice(0, 2);
                    if (stableClasses.length > 0) cssSelector += '.' + stableClasses.join('.');
                }
            }

            // Compute depth relative to document.body
            let depth = 0;
            let ancestor = el.parentElement;
            while (ancestor && ancestor !== document.body) {
                depth++;
                ancestor = ancestor.parentElement;
            }

            elements.push({
                tag_name: el.tagName.toLowerCase(),
                text: text,
                attributes: attrs,
                is_visible: isVisible,
                is_enabled: !el.disabled,
                is_focused: document.activeElement === el,
                bounding_box: isVisible ? {
                    x: Math.round(rect.x),
                    y: Math.round(rect.y),
                    width: Math.round(rect.width),
                    height: Math.round(rect.height),
                } : null,
                parent_context: parentContext,
                children_count: el.querySelectorAll('a, button, input, textarea, select').length,
                css_selector: cssSelector,
                is_leaf: el.querySelectorAll('a, button, input, textarea, select, [role="button"], [role="link"]').length === 0,
                depth: depth,
            });
        }

    // Extract forms
    const forms = [];
    for (const form of document.querySelectorAll('form')) {
        const formEls = form.querySelectorAll('input, textarea, select, button');
        forms.push({
            name: form.getAttribute('name') || form.getAttribute('id') || 'unnamed',
            action: form.getAttribute('action') || '',
            method: (form.getAttribute('method') || 'GET').toUpperCase(),
        });
    }

    // Page text summary — larger for content-rich pages (search results, articles)
    const bodyText = (document.body.innerText || '').trim();
    const textSummary = bodyText.substring(0, 1500);

    // Scroll info
    const scrollHeight = document.documentElement.scrollHeight;
    const clientHeight = document.documentElement.clientHeight;
    const scrollTop = window.scrollY;
    const scrollPos = scrollHeight > clientHeight ? scrollTop / (scrollHeight - clientHeight) : 0;

    return {
        title: document.title,
        url: window.location.href,
        meta_description: document.querySelector('meta[name="description"]')?.content || '',
        page_text_summary: textSummary,
        viewport_width: window.innerWidth,
        viewport_height: window.innerHeight,
        scroll_position: Math.min(1, Math.max(0, scrollPos)),
        has_more_content_below: (scrollTop + clientHeight) < (scrollHeight - 50),
        elements: elements,
        forms: forms,
    };
}
"""


def _resolve_element_type(tag: str, attrs: dict[str, str]) -> ElementType:
    """Determine ElementType from tag name and attributes."""
    role = attrs.get("role", "")

    # Role-based detection first
    if role == "button":
        return ElementType.BUTTON
    if role == "link":
        return ElementType.LINK
    if role == "tab":
        return ElementType.TAB
    if role == "menuitem":
        return ElementType.MENU_ITEM
    if role == "checkbox":
        return ElementType.CHECKBOX
    if role == "radio":
        return ElementType.RADIO
    if role == "switch":
        return ElementType.TOGGLE
    if role == "slider":
        return ElementType.SLIDER

    # Input type detection
    if tag == "input":
        input_type = attrs.get("type", "text").lower()
        return _INPUT_TYPE_MAP.get(input_type, ElementType.TEXT_INPUT)

    # Tag-based detection
    if tag in _TAG_TO_TYPE:
        return _TAG_TO_TYPE[tag]

    # Clickable divs/spans with onclick or tabindex
    if attrs.get("onclick") or attrs.get("tabindex"):
        return ElementType.BUTTON

    return ElementType.OTHER


async def extract_page_context(page: Page, max_elements: int = 200) -> PageContext:
    """Extract the current page state into a PageContext.

    Args:
        page: Playwright Page object
        max_elements: Max elements to include (prevents context window bloat)

    Returns:
        PageContext matching the exact schema the agent expects
    """
    raw = await page.evaluate(_EXTRACT_JS)

    # Assign element_ids and build DOMElement objects
    dom_elements: list[DOMElement] = []
    for idx, el_data in enumerate(raw["elements"][:max_elements], start=1):
        tag = el_data["tag_name"]
        attrs = el_data.get("attributes", {})
        el_type = _resolve_element_type(tag, attrs)

        bbox = None
        if el_data.get("bounding_box"):
            bbox = BoundingBox(**el_data["bounding_box"])

        dom_elements.append(DOMElement(
            element_id=idx,
            element_type=el_type,
            tag_name=tag,
            text=el_data.get("text", ""),
            attributes=attrs,
            is_visible=el_data.get("is_visible", True),
            is_enabled=el_data.get("is_enabled", True),
            is_focused=el_data.get("is_focused", False),
            bounding_box=bbox,
            parent_context=el_data.get("parent_context", ""),
            children_count=el_data.get("children_count", 0),
            css_selector=el_data.get("css_selector", ""),
            is_leaf=el_data.get("is_leaf", True),
            depth=el_data.get("depth", 0),
        ))

    # Map form field_ids (forms reference element positions, not IDs)
    forms_data = []
    for form in raw.get("forms", []):
        forms_data.append({
            "name": form.get("name", "unnamed"),
            "action": form.get("action", ""),
            "method": form.get("method", "GET"),
            "field_ids": [],  # Populated by extension; Playwright doesn't link these
        })

    return PageContext(
        url=raw["url"],
        title=raw["title"],
        meta_description=raw.get("meta_description", ""),
        page_text_summary=raw.get("page_text_summary", ""),
        elements=dom_elements,
        forms=forms_data,
        viewport_width=raw.get("viewport_width", 1920),
        viewport_height=raw.get("viewport_height", 1080),
        scroll_position=raw.get("scroll_position", 0.0),
        has_more_content_below=raw.get("has_more_content_below", False),
        timestamp=time.time(),
    )
