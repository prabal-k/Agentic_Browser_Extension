"""Action executor — Executes agent actions on a real Playwright page.

Receives an Action from the agent, maps element_id back to a Playwright locator,
executes the action, and returns an ActionResult with the outcome.

The element_id-to-locator mapping uses the CSS selectors stored during DOM extraction.
"""

import base64
import time
import structlog
from playwright.async_api import Page, TimeoutError as PlaywrightTimeout

from agent_core.schemas.actions import Action, ActionResult, ActionStatus, ActionType
from agent_core.schemas.dom import PageContext

logger = structlog.get_logger("playwright.executor")


async def _analyze_screenshot_with_vision(
    screenshot_bytes: bytes,
    query: str,
    page_url: str,
) -> str:
    """Send a screenshot to the vision model for analysis.

    Uses the Ollama HTTP API directly (bypasses langchain) because
    langchain_ollama has issues with image serialization in some versions.
    """
    try:
        from agent_core.config import settings
        import httpx

        vision_model = settings.vision_model
        if not vision_model:
            return "Vision model not configured. Set AGENT_VISION_MODEL in .env."

        base_url = settings.ollama_base_url
        img_b64 = base64.b64encode(screenshot_bytes).decode("utf-8")

        # The query comes from the agent's tool call description —
        # it carries the actual task context (e.g., "check for vape products")
        prompt = (
            f"Page URL: {page_url}\n\n"
            f"Task: {query}\n\n"
            "Look at this screenshot and answer the task above. "
            "Be specific and factual about what you see. "
            "If the task asks about specific products, items, or content — "
            "say whether you can see them or not, and describe any evidence."
        )

        payload = {
            "model": vision_model,
            "messages": [{
                "role": "user",
                "content": prompt,
                "images": [img_b64],
            }],
            "stream": False,
        }

        async with httpx.AsyncClient(timeout=120.0) as client:
            resp = await client.post(f"{base_url}/api/chat", json=payload)
            resp.raise_for_status()
            data = resp.json()

        result = data.get("message", {}).get("content", "")
        logger.info("vision_analysis_complete", url=page_url, result_length=len(result))
        return result[:3000] if result else "Vision model returned empty response."

    except Exception as e:
        logger.error("vision_analysis_error", error=str(e))
        return f"Vision analysis failed: {str(e)[:200]}"


def _find_element_selector(page_context: PageContext, element_id: int) -> str | None:
    """Find the CSS selector for an element_id from the last DOM snapshot."""
    for el in page_context.elements:
        if el.element_id == element_id:
            # Prefer CSS selector, fall back to xpath, then tag-based
            if el.css_selector:
                return el.css_selector
            if el.xpath:
                return el.xpath
            # Last resort: construct from tag + text
            if el.text:
                return f"{el.tag_name}:has-text(\"{el.text[:50]}\")"
            return el.tag_name
    return None


_EXTRACT_JS = r"""() => {
    // =============================================================
    // Universal Listing Extractor v2
    // Works on: e-commerce, Google Maps, directories, job boards, etc.
    //
    // Strategy 0: Semantic feeds (role="feed", aria-label lists, Maps places)
    // Strategy 1: JSON-LD / Schema.org structured data
    // Strategy 2: Signal-anchored DOM walking (prices, ratings, reviews)
    // Strategy 3: Card container detection with scoring
    // =============================================================

    const MAX_ITEMS = 80;

    function resolveUrl(href) {
        if (!href) return '';
        if (href.startsWith('http')) return href;
        if (href.startsWith('//')) return 'https:' + href;
        if (href.startsWith('/')) return window.location.origin + href;
        return href;
    }

    function hasPrice(text) {
        return /(?:Rs\.\s*|Rs\s+|NPR\s*|\$|USD\s*|EUR\s*|€|£|¥|₹|৳|kr\s)[\d,]+/.test(text)
            || /[\d,]+(?:\.\d{1,2})?\s*(?:Rs\.|NPR|USD|EUR|GBP)/.test(text)
            || /[^\x00-\x7F]\s*[\d,]+\.?\d{0,2}/.test(text) && text.length < 30;
    }

    function extractPrice(el) {
        const text = el.innerText || '';
        let m = text.match(/(?:Rs\.\s*|Rs\s+|NPR\s*|\$|USD\s*|EUR\s*|€|£|¥|₹|৳|kr\s*)[\d,]+(?:\.\d{1,2})?/i);
        if (m) return m[0].trim();
        m = text.match(/[\d,]+(?:\.\d{1,2})?\s*(?:Rs\.|NPR|USD|EUR|GBP)/i);
        if (m) return m[0].trim();
        m = text.match(/[^\x00-\x7F]\s*[\d,]+(?:\.[\d]{1,2})?/);
        if (m) return m[0].trim();
        return '';
    }

    function extractName(el) {
        // 1. aria-label on the element itself or its first link
        const ariaLabel = el.getAttribute('aria-label');
        if (ariaLabel && ariaLabel.length > 3) return ariaLabel;
        const labeledLink = el.querySelector('a[aria-label]');
        if (labeledLink) {
            const lbl = labeledLink.getAttribute('aria-label');
            if (lbl && lbl.length > 3) return lbl;
        }
        // 2. Heading inside element
        const heading = el.querySelector('h1,h2,h3,h4,h5,h6');
        if (heading && heading.innerText.trim().length > 3) return heading.innerText.trim();
        // 3. Element with name/title class
        const named = el.querySelector('[class*="name"],[class*="title"],[class*="Name"],[class*="Title"]');
        if (named && named.innerText.trim().length > 3) return named.innerText.trim();
        // 4. Longest link text
        let bestLink = '';
        for (const a of el.querySelectorAll('a[href]')) {
            const t = a.innerText.trim();
            if (t.length > bestLink.length && t.length > 5 && !hasPrice(t)) bestLink = t;
        }
        if (bestLink) return bestLink;
        // 5. Image alt text
        const img = el.querySelector('img[alt]');
        if (img && img.alt.trim().length > 3) return img.alt.trim();
        // 6. First meaningful text node
        const allText = (el.innerText || '').trim();
        if (allText.length > 5 && allText.length < 200) return allText.split('\n')[0].trim();
        return '';
    }

    function extractFromCard(el) {
        const item = {};
        item.name = extractName(el).substring(0, 250);
        // URL
        const link = el.tagName === 'A' ? el : el.querySelector('a[href]');
        if (link) item.url = resolveUrl(link.getAttribute('href'));
        // Price
        item.price = extractPrice(el);
        // Original price
        const del_ = el.querySelector('del, s, [class*="original"], [class*="old-price"]');
        if (del_) item.original_price = (del_.innerText || '').trim();
        // Discount
        const discMatch = (el.innerText || '').match(/-?\d{1,3}%/);
        if (discMatch) item.discount = discMatch[0];
        // Image
        const img = el.querySelector('img');
        if (img) {
            const src = img.getAttribute('src') || img.getAttribute('data-src') || img.dataset?.src || '';
            if (src && !src.startsWith('data:image/svg')) item.image_url = resolveUrl(src);
        }
        // Rating — handle both ASCII and Unicode numerals (Devanagari etc.)
        const ratingEl = el.querySelector('[class*="rating"],[class*="star"],[aria-label*="star"],[aria-label*="rating"],[role="img"]');
        if (ratingEl) {
            let rt = ratingEl.getAttribute('aria-label') || ratingEl.innerText || '';
            // Convert Devanagari/non-ASCII numerals to ASCII
            rt = rt.replace(/[\u0966-\u096F]/g, c => String(c.charCodeAt(0) - 0x0966));
            const rm = rt.match(/(\d+\.?\d*)/);
            if (rm) item.rating = rm[1];
        }
        // Reviews
        const reviewMatch = (el.innerText || '').match(/\(?([\d,]+)\)?\s*(?:review|rating|sold|revi)/i);
        if (reviewMatch) item.reviews = reviewMatch[1];
        // Address/description — short text that's not name or price
        const texts = (el.innerText || '').split('\n').map(l => l.trim()).filter(l => l.length > 5 && l.length < 150);
        const desc = texts.find(t => t !== item.name && !hasPrice(t) && t.length > 10);
        if (desc) item.description = desc.substring(0, 200);
        return item;
    }

    // =============================================================
    // STRATEGY 0: Semantic feeds & structured lists
    // Google Maps, accessible lists, role="feed" containers
    // =============================================================
    function trySemanticFeed() {
        // Google Maps: links to places — card is parent div of the link
        const mapLinks = document.querySelectorAll('a[href*="/maps/place"]');
        if (mapLinks.length >= 2) {
            const items = [];
            const seen = new Set();
            for (const a of mapLinks) {
                if (items.length >= MAX_ITEMS) break;
                // Walk up to find the card with images+ratings (usually 1 level up)
                let card = a.parentElement || a;
                for (let i = 0; i < 3; i++) {
                    if (card.querySelector('img') || card.querySelector('[role="img"]')) break;
                    if (card.parentElement && card.parentElement !== document.body) card = card.parentElement;
                    else break;
                }
                if (seen.has(card)) continue;
                seen.add(card);
                const data = extractFromCard(card);
                // Fallback name from aria-label on the link
                if (!data.name && a.getAttribute('aria-label')) data.name = a.getAttribute('aria-label');
                // URL from the maps link
                if (!data.url) data.url = a.getAttribute('href') || '';
                if (data.name) items.push(data);
            }
            if (items.length >= 2) return items;
        }

        // role="feed" container
        const feed = document.querySelector('[role="feed"]');
        if (feed && feed.children.length >= 3) {
            const items = [];
            for (const child of feed.children) {
                if (items.length >= MAX_ITEMS) break;
                const data = extractFromCard(child);
                if (data.name) items.push(data);
            }
            if (items.length >= 2) return items;
        }

        // role="list" with role="listitem" children
        const lists = document.querySelectorAll('[role="list"]');
        for (const list of lists) {
            const listItems = list.querySelectorAll('[role="listitem"]');
            if (listItems.length >= 3) {
                const items = [];
                for (const li of listItems) {
                    if (items.length >= MAX_ITEMS) break;
                    const data = extractFromCard(li);
                    if (data.name) items.push(data);
                }
                if (items.length >= 2) return items;
            }
        }

        return null;
    }

    // =============================================================
    // STRATEGY 1: JSON-LD / Schema.org
    // =============================================================
    function tryJsonLd() {
        const items = [];
        for (const script of document.querySelectorAll('script[type="application/ld+json"]')) {
            try {
                let data = JSON.parse(script.textContent);
                if (data['@graph']) data = data['@graph'];
                const arr = Array.isArray(data) ? data : [data];
                for (const obj of arr) {
                    if (obj['@type'] === 'ItemList' && obj.itemListElement) {
                        for (const li of obj.itemListElement) {
                            const prod = li.item || li;
                            if (prod.name) items.push({
                                name: prod.name,
                                url: resolveUrl(prod.url || ''),
                                price: prod.offers?.price ? (prod.offers.priceCurrency || '') + ' ' + prod.offers.price : '',
                                image_url: resolveUrl(Array.isArray(prod.image) ? prod.image[0] : prod.image || ''),
                                rating: prod.aggregateRating?.ratingValue || '',
                                reviews: prod.aggregateRating?.reviewCount || '',
                            });
                        }
                    }
                    if (obj['@type'] === 'Product' && obj.name) {
                        items.push({
                            name: obj.name,
                            url: resolveUrl(obj.url || ''),
                            price: obj.offers?.price ? (obj.offers.priceCurrency || '') + ' ' + obj.offers.price : '',
                        });
                    }
                }
            } catch(e) {}
        }
        // Only use if items have prices (>50% with prices)
        if (items.length >= 2) {
            const withPrices = items.filter(i => i.price && i.price.trim().length > 0).length;
            if (withPrices >= items.length * 0.3) return items;
        }
        return null;
    }

    // =============================================================
    // STRATEGY 2: Signal-anchored DOM walking
    // Find elements with prices, ratings, or links — walk up to cards
    // =============================================================
    function trySignalAnchored() {
        const signalEls = [];
        const allEls = document.querySelectorAll('span, div, p, strong, b, em, ins, del, s, td, bdi, .woocommerce-Price-amount, .price');
        for (const el of allEls) {
            if (signalEls.length >= 200) break;
            const text = (el.innerText || el.textContent || '').trim();
            if (text.length >= 4 && text.length <= 60 && hasPrice(text)) {
                signalEls.push(el);
            }
        }

        if (signalEls.length < 2) return null;

        const seen = new Set();
        const cards = [];
        for (const sigEl of signalEls) {
            let card = sigEl;
            let bestCard = null;
            for (let i = 0; i < 8; i++) {
                card = card.parentElement;
                if (!card || card === document.body) break;
                const textLen = (card.innerText || '').length;
                if (textLen > 1500) break;
                const hasLink = !!card.querySelector('a[href]');
                const hasImg = !!card.querySelector('img');
                const isNav = !!card.closest('nav, aside, [role="navigation"], [class*="sidebar"], [class*="filter"], [class*="facet"]');
                if (isNav) continue;
                if (hasLink && hasImg) { bestCard = card; break; }
                if (hasLink || hasImg) bestCard = card;
            }
            const finalCard = bestCard || card;
            if (finalCard && finalCard !== document.body && !seen.has(finalCard)) {
                const tLen = (finalCard.innerText || '').length;
                if (tLen > 10 && tLen < 1500) {
                    seen.add(finalCard);
                    cards.push(finalCard);
                }
            }
        }

        if (cards.length < 2) return null;

        const items = [];
        for (let i = 0; i < Math.min(cards.length, MAX_ITEMS); i++) {
            const data = extractFromCard(cards[i]);
            if (data.name || data.price) items.push(data);
        }
        return items.length >= 2 ? items : null;
    }

    // =============================================================
    // STRATEGY 3: Card container detection (class-based grouping)
    // =============================================================
    function tryCardDetection() {
        const candidates = document.querySelectorAll(
            '[data-qa-locator="product-item"], [data-tracking], ' +
            '[data-component="search"], [data-component="product"], ' +
            '[class*="product-card"], [class*="product-item"], [class*="productCard"], ' +
            '[class*="search-product"], [class*="s-result-item"], ' +
            '[class*="product"], [class*="card"], [class*="listing"], [class*="result"], ' +
            'ul > li, ol > li, [role="list"] > [role="listitem"]'
        );

        const groups = new Map();
        for (const el of candidates) {
            const parent = el.parentElement;
            if (!parent) continue;
            const sig = parent.tagName + '|' + el.tagName + '|' +
                (el.className || '').toString().trim().split(/\s+/).sort().join(' ');
            if (!groups.has(sig)) groups.set(sig, []);
            groups.get(sig).push(el);
        }

        function scoreGroup(els) {
            if (els.length < 3) return -1;
            let priceHits = 0, imgHits = 0, linkHits = 0;
            const sample = els.slice(0, Math.min(els.length, 8));
            for (const el of sample) {
                if (hasPrice(el.innerText || '')) priceHits++;
                if (el.querySelector('img')) imgHits++;
                if (el.querySelector('a[href]')) linkHits++;
            }
            const pp = priceHits / sample.length;
            const ip = imgHits / sample.length;
            const lp = linkHits / sample.length;
            return els.length + (pp * els.length * 10) + (ip * els.length * 3) + (lp * els.length * 2);
        }

        let bestGroup = [], bestScore = -1;
        for (const [, els] of groups) {
            const s = scoreGroup(els);
            if (s > bestScore) { bestScore = s; bestGroup = els; }
        }

        if (bestGroup.length < 2) return null;

        const items = [];
        for (let i = 0; i < Math.min(bestGroup.length, MAX_ITEMS); i++) {
            const data = extractFromCard(bestGroup[i]);
            if (data.name || data.price) items.push(data);
        }
        return items.length >= 2 ? items : null;
    }

    // =============================================================
    // RUN STRATEGIES in priority order
    // =============================================================
    let items = null;
    let strategy = '';

    // Strategy 0: Semantic feeds (Google Maps, accessible lists)
    try { items = trySemanticFeed(); } catch(e) {}
    if (items && items.length >= 2) strategy = 'semantic-feed';

    // Strategy 1: JSON-LD
    if (!items) {
        try { items = tryJsonLd(); } catch(e) {}
        if (items && items.length >= 2) strategy = 'json-ld';
        else items = null;
    }

    // Strategy 2: Signal-anchored (prices, ratings)
    if (!items) {
        try { items = trySignalAnchored(); } catch(e) {}
        if (items && items.length >= 2) strategy = 'signal-anchored';
        else items = null;
    }

    // Strategy 3: Card detection
    if (!items) {
        try { items = tryCardDetection(); } catch(e) {}
        if (items && items.length >= 2) strategy = 'card-detection';
        else items = null;
    }

    if (!items || items.length === 0) {
        return JSON.stringify({error: "No listing structure detected. Try read_page or visual_check instead."});
    }

    // Post-filter: remove nav/filter items and deduplicate
    const navWords = /^(price|filter|category|sort|shop on|popular|brand|condition|format|type|color|see all|show more|sponsored|view all)$/i;
    items = items.filter(item => {
        const name = (item.name || '').trim();
        if (name.length > 0 && name.length < 5 && !item.price) return false;
        if (navWords.test(name)) return false;
        return true;
    });
    const seenUrls = new Set();
    items = items.filter(item => {
        if (!item.url) return true;
        if (seenUrls.has(item.url)) return false;
        seenUrls.add(item.url);
        return true;
    });

    return JSON.stringify({
        strategy: strategy,
        total_items: items.length,
        page_url: window.location.href,
        items: items
    }, null, 2);
}"""


async def _extract_listings_with_scroll(page: Page, max_scrolls: int = 5) -> str:
    """Extract listings with auto-scroll to load more items.

    For scrollable containers (Google Maps, infinite scroll pages),
    scrolls the feed/page multiple times before extracting.
    """
    import json as _json

    # Step 1: Detect scrollable feed container
    feed_selector = await page.evaluate("""() => {
        const feed = document.querySelector('[role="feed"]');
        if (feed) return '[role="feed"]';
        const scrollables = document.querySelectorAll('[style*="overflow"], [class*="scroll"]');
        for (const el of scrollables) {
            const style = window.getComputedStyle(el);
            if ((style.overflowY === 'auto' || style.overflowY === 'scroll') && el.scrollHeight > el.clientHeight + 100) {
                if (el.querySelectorAll('a[href]').length >= 3) {
                    return el.id ? '#' + el.id : null;
                }
            }
        }
        return null;
    }""")

    # Step 2: Scroll to load more items
    for i in range(max_scrolls):
        if feed_selector:
            # Scroll inside the feed container
            await page.evaluate(f"""(sel) => {{
                const el = document.querySelector(sel);
                if (el) el.scrollTop = el.scrollHeight;
            }}""", feed_selector)
        else:
            # Scroll the page
            await page.evaluate("window.scrollTo(0, document.documentElement.scrollHeight)")

        await page.wait_for_timeout(1500)

        # Check if new content loaded
        new_count = await page.evaluate("""() => {
            return document.querySelectorAll('a[href]').length;
        }""")
        if i > 0:
            prev_count = getattr(_extract_listings_with_scroll, '_prev_count', 0)
            if new_count == prev_count:
                break  # No new content, stop scrolling
        _extract_listings_with_scroll._prev_count = new_count

    # Step 3: Scroll back to top so extraction covers everything
    if feed_selector:
        await page.evaluate(f"""(sel) => {{
            const el = document.querySelector(sel);
            if (el) el.scrollTop = 0;
        }}""", feed_selector)
    else:
        await page.evaluate("window.scrollTo(0, 0)")

    await page.wait_for_timeout(500)

    # Step 4: Run extraction JS
    return await page.evaluate(_EXTRACT_JS)


async def execute_action(
    page: Page,
    action: Action,
    page_context: PageContext,
    timeout_ms: int = 10000,
) -> ActionResult:
    """Execute a browser action on the Playwright page.

    Args:
        page: Playwright Page object
        action: Action from the agent
        page_context: Last DOM snapshot (for element_id → selector mapping)
        timeout_ms: Max wait time for actions

    Returns:
        ActionResult with status, message, and page change info
    """
    start_time = time.time()
    old_url = page.url

    try:
        result = await _dispatch_action(page, action, page_context, timeout_ms)
    except PlaywrightTimeout:
        return ActionResult(
            action_id=action.action_id,
            status=ActionStatus.TIMEOUT,
            message=f"Action timed out after {timeout_ms}ms",
            error=f"Timeout executing {action.action_type.value}",
            execution_time_ms=(time.time() - start_time) * 1000,
        )
    except Exception as e:
        logger.error("action_execution_error",
                     action_type=action.action_type.value,
                     element_id=action.element_id,
                     error=str(e))
        return ActionResult(
            action_id=action.action_id,
            status=ActionStatus.FAILED,
            message=f"Action failed: {str(e)}",
            error=str(e),
            execution_time_ms=(time.time() - start_time) * 1000,
        )

    # Check if page changed
    new_url = page.url
    page_changed = new_url != old_url

    result.execution_time_ms = (time.time() - start_time) * 1000
    result.page_changed = page_changed
    if page_changed:
        result.new_url = new_url

    return result


def _is_search_input(page_context: PageContext, element_id: int | None) -> bool:
    """Check if an element is a search-type input (safe to auto-submit).

    Detects: input[type="search"], input[name="q"], input[role="searchbox"],
    inputs with search-related placeholders, or the only text input on the page.
    """
    if element_id is None:
        return False

    target_el = None
    text_input_count = 0

    for el in page_context.elements:
        if el.element_id == element_id:
            target_el = el
        # Count text inputs on the page
        if el.element_type.value in ("text_input", "textarea"):
            text_input_count += 1

    if not target_el:
        return False

    attrs = target_el.attributes
    el_type = attrs.get("type", "").lower()
    el_name = attrs.get("name", "").lower()
    el_role = attrs.get("role", "").lower()
    el_placeholder = attrs.get("placeholder", "").lower()

    # Explicit search indicators
    if el_type == "search":
        return True
    if el_role == "searchbox" or el_role == "search":
        return True
    if el_name in ("q", "query", "search", "search_query", "keyword", "keywords"):
        return True
    if any(w in el_placeholder for w in ("search", "find", "look for", "type to search")):
        return True

    # If this is the only text input on the page, it's likely a search bar
    if text_input_count == 1 and target_el.element_type.value == "text_input":
        return True

    return False


async def _dispatch_action(
    page: Page,
    action: Action,
    page_context: PageContext,
    timeout_ms: int,
) -> ActionResult:
    """Route action to the appropriate Playwright method."""
    at = action.action_type

    # --- Element-targeted actions ---
    if at in (
        ActionType.CLICK, ActionType.CLEAR_AND_TYPE,
        ActionType.SELECT_OPTION, ActionType.CHECK, ActionType.UNCHECK,
        ActionType.HOVER, ActionType.SCROLL_TO_ELEMENT,
    ):
        if action.element_id is None:
            return ActionResult(
                action_id=action.action_id,
                status=ActionStatus.FAILED,
                message="element_id is required for this action",
                error="Missing element_id",
            )

        selector = _find_element_selector(page_context, action.element_id)
        if not selector:
            return ActionResult(
                action_id=action.action_id,
                status=ActionStatus.ELEMENT_NOT_FOUND,
                message=f"Element {action.element_id} not found in DOM snapshot",
                error=f"No selector for element_id={action.element_id}",
            )

        locator = page.locator(selector).first

        # Verify element exists on page
        try:
            await locator.wait_for(state="attached", timeout=3000)
        except PlaywrightTimeout:
            return ActionResult(
                action_id=action.action_id,
                status=ActionStatus.ELEMENT_NOT_FOUND,
                message=f"Element '{selector}' not found on page",
                error=f"Selector not found: {selector}",
            )

        if at == ActionType.CLICK:
            used_fallback = False
            try:
                await locator.click(timeout=timeout_ms)
            except PlaywrightTimeout:
                # Playwright click timed out — element is in DOM but failed
                # actionability checks (covered, animating, etc.).
                # Try force click first (real mouse event, skips checks),
                # then JS click as last resort.
                logger.info("click_fallback_force",
                            element_id=action.element_id,
                            selector=selector)
                try:
                    await locator.click(force=True, timeout=5000)
                except Exception:
                    # Force click also failed — use JS with full event chain
                    logger.info("click_fallback_js",
                                element_id=action.element_id,
                                selector=selector)
                    await locator.evaluate(
                        "el => { el.dispatchEvent(new MouseEvent('mousedown', {bubbles:true}));"
                        " el.dispatchEvent(new MouseEvent('mouseup', {bubbles:true}));"
                        " el.click(); }"
                    )
                used_fallback = True

            if used_fallback:
                # Fallback clicks may not trigger Playwright navigation detection.
                # Poll briefly for SPA route changes (login redirects, etc.)
                pre_url = page.url
                for _ in range(6):  # up to 3s
                    await page.wait_for_timeout(500)
                    if page.url != pre_url:
                        break

            return ActionResult(
                action_id=action.action_id,
                status=ActionStatus.SUCCESS,
                message=f"Clicked element {action.element_id}",
            )

        elif at in (ActionType.CLEAR_AND_TYPE, ActionType.TYPE_TEXT):
            raw_value = action.value or ""
            should_submit = raw_value.endswith("|SUBMIT")
            text = raw_value.replace("|SUBMIT", "") if should_submit else raw_value

            # Auto-submit for search inputs even if LLM forgot submit=True
            if not should_submit and _is_search_input(page_context, action.element_id):
                should_submit = True
                logger.info("auto_submit_search", element_id=action.element_id)

            # Clear: try fill("") first, fall back to select-all + delete
            try:
                await locator.fill("", timeout=timeout_ms)
            except Exception:
                await locator.click(timeout=timeout_ms)
                await page.keyboard.press("Control+a")
                await page.keyboard.press("Backspace")

            # Type: try fill() first, fall back to keyboard.type
            try:
                await locator.fill(text, timeout=timeout_ms)
            except Exception:
                await page.keyboard.type(text, delay=30)

            if should_submit:
                await page.wait_for_timeout(100)
                await page.keyboard.press("Enter")

            return ActionResult(
                action_id=action.action_id,
                status=ActionStatus.SUCCESS,
                message=f"Cleared and typed '{text}' into element {action.element_id}{' and submitted' if should_submit else ''}",
            )

        elif at == ActionType.SELECT_OPTION:
            await locator.select_option(value=action.value, timeout=timeout_ms)
            return ActionResult(
                action_id=action.action_id,
                status=ActionStatus.SUCCESS,
                message=f"Selected option '{action.value}' on element {action.element_id}",
            )

        elif at == ActionType.CHECK:
            await locator.check(timeout=timeout_ms)
            return ActionResult(
                action_id=action.action_id,
                status=ActionStatus.SUCCESS,
                message=f"Checked element {action.element_id}",
            )

        elif at == ActionType.UNCHECK:
            await locator.uncheck(timeout=timeout_ms)
            return ActionResult(
                action_id=action.action_id,
                status=ActionStatus.SUCCESS,
                message=f"Unchecked element {action.element_id}",
            )

        elif at == ActionType.HOVER:
            await locator.hover(timeout=timeout_ms)
            return ActionResult(
                action_id=action.action_id,
                status=ActionStatus.SUCCESS,
                message=f"Hovered over element {action.element_id}",
            )

        elif at == ActionType.SCROLL_TO_ELEMENT:
            await locator.scroll_into_view_if_needed(timeout=timeout_ms)
            return ActionResult(
                action_id=action.action_id,
                status=ActionStatus.SUCCESS,
                message=f"Scrolled to element {action.element_id}",
            )

    # --- Navigation actions ---
    elif at == ActionType.NAVIGATE:
        url = action.value or ""
        if not url.startswith(("http://", "https://")):
            url = "https://" + url
        await page.goto(url, timeout=timeout_ms, wait_until="domcontentloaded")
        return ActionResult(
            action_id=action.action_id,
            status=ActionStatus.SUCCESS,
            message=f"Navigated to {url}",
            new_url=page.url,
            page_changed=True,
        )

    elif at == ActionType.GO_BACK:
        await page.go_back(timeout=timeout_ms, wait_until="domcontentloaded")
        return ActionResult(
            action_id=action.action_id,
            status=ActionStatus.SUCCESS,
            message="Navigated back",
        )

    elif at == ActionType.GO_FORWARD:
        await page.go_forward(timeout=timeout_ms, wait_until="domcontentloaded")
        return ActionResult(
            action_id=action.action_id,
            status=ActionStatus.SUCCESS,
            message="Navigated forward",
        )

    elif at == ActionType.REFRESH:
        await page.reload(timeout=timeout_ms, wait_until="domcontentloaded")
        return ActionResult(
            action_id=action.action_id,
            status=ActionStatus.SUCCESS,
            message="Page refreshed",
        )

    # --- Scroll actions ---
    elif at == ActionType.SCROLL_DOWN:
        await page.evaluate("window.scrollBy(0, window.innerHeight * 0.8)")
        return ActionResult(
            action_id=action.action_id,
            status=ActionStatus.SUCCESS,
            message="Scrolled down",
        )

    elif at == ActionType.SCROLL_UP:
        await page.evaluate("window.scrollBy(0, -window.innerHeight * 0.8)")
        return ActionResult(
            action_id=action.action_id,
            status=ActionStatus.SUCCESS,
            message="Scrolled up",
        )

    # --- Keyboard actions ---
    elif at == ActionType.PRESS_KEY:
        await page.keyboard.press(action.value or "Enter")
        return ActionResult(
            action_id=action.action_id,
            status=ActionStatus.SUCCESS,
            message=f"Pressed key: {action.value}",
        )

    elif at == ActionType.KEY_COMBO:
        # e.g. "Ctrl+A" or "Shift+Enter"
        combo = action.value or ""
        keys = combo.split("+")
        for key in keys[:-1]:
            await page.keyboard.down(key.strip())
        await page.keyboard.press(keys[-1].strip())
        for key in reversed(keys[:-1]):
            await page.keyboard.up(key.strip())
        return ActionResult(
            action_id=action.action_id,
            status=ActionStatus.SUCCESS,
            message=f"Pressed key combo: {combo}",
        )

    # --- Information gathering ---
    elif at == ActionType.EXTRACT_TEXT:
        # extract_listings tool — auto-detect repeated card structures
        if action.value == "__EXTRACT_LISTINGS__":
            text = await _extract_listings_with_scroll(page)
            return ActionResult(
                action_id=action.action_id,
                status=ActionStatus.SUCCESS,
                message=f"Extracted {text[:50]}..." if len(text) > 50 else "Extracted listings data",
                extracted_data=text,
            )

        # Placeholder to preserve the old code below as dead code — remove after confirming
        if False:  # noqa — old inline JS (kept for reference)
            text = await page.evaluate("""() => {
                // =============================================================
                // OLD Universal Listing Extractor (replaced by _extract_listings_with_scroll)
                // Works on ANY e-commerce/listing website.
                //
                // Strategy 1: JSON-LD / Schema.org structured data (highest quality)
                // Strategy 2: Price-anchored DOM walking (most universal)
                // Strategy 3: Card container detection with scoring (fallback)
                // =============================================================

                const PRICE_RE = /(?:Rs\\.\\s*|Rs\\s+|NPR\\s*|\\$|USD\\s*|EUR\\s*|€|£|¥|₹|\\u20B9|रू\\.?\\s*|\\bKRW\\s*|\\bINR\\s*|\\bAUD\\s*|\\bCAD\\s*|\\bGBP\\s*)[\\d,]+(?:\\.\\d{1,2})?|[\\d,]+(?:\\.\\d{1,2})?\\s*(?:Rs\\.|NPR|USD|EUR|GBP)/i;
                const MAX_ITEMS = 60;

                // --- Helpers ---
                function resolveUrl(href) {
                    if (!href) return '';
                    if (href.startsWith('http')) return href;
                    if (href.startsWith('//')) return 'https:' + href;
                    if (href.startsWith('/')) return window.location.origin + href;
                    return href;
                }

                function resolveImgUrl(img) {
                    if (!img) return '';
                    const src = img.getAttribute('src') || img.getAttribute('data-src') ||
                                img.getAttribute('data-lazy-src') || img.getAttribute('data-original') ||
                                img.dataset.src || '';
                    return resolveUrl(src);
                }

                function extractName(el) {
                    // 1. Heading inside element
                    const heading = el.querySelector('h1,h2,h3,h4,h5,h6');
                    if (heading && heading.innerText.trim().length > 3) return heading.innerText.trim();

                    // 2. Element with name/title class
                    const named = el.querySelector('[class*="name"],[class*="title"],[class*="Name"],[class*="Title"]');
                    if (named && named.innerText.trim().length > 3) return named.innerText.trim();

                    // 3. Longest link text (product links are descriptive)
                    let bestLink = '';
                    for (const a of el.querySelectorAll('a[href]')) {
                        const t = a.innerText.trim();
                        if (t.length > bestLink.length && t.length > 5 && !/(?:Rs\.|Rs\s|\$|€|£|¥|₹|\u20A8)[\d,]+/.test(t)) bestLink = t;
                    }
                    if (bestLink) return bestLink;

                    // 4. Image alt text
                    const img = el.querySelector('img[alt]');
                    if (img && img.alt.trim().length > 3) return img.alt.trim();

                    // 5. Extract from URL slug
                    const link = el.querySelector('a[href]');
                    if (link) {
                        const slug = link.getAttribute('href').split('/').filter(s => s.length > 10).pop() || '';
                        return slug.replace(/[-_]/g, ' ').replace(/\\.html.*/, '').replace(/i\\d+$/, '').trim();
                    }
                    return '';
                }

                function extractPrice(el) {
                    const text = el.innerText || '';
                    // Try standard currency patterns
                    let match = text.match(/(?:Rs\.\s*|Rs\s+|NPR\s*|\$|USD\s*|EUR\s*|€|£|¥|₹|৳|kr\s*)[\d,]+(?:\.\d{1,2})?/i);
                    if (match) return match[0].trim();
                    match = text.match(/[\d,]+(?:\.\d{1,2})?\s*(?:Rs\.|NPR|USD|EUR|GBP)/i);
                    if (match) return match[0].trim();
                    // Catch-all: non-ASCII currency symbol + digits
                    match = text.match(/[^\x00-\x7F][\s]*[\d,]+(?:\.[\d]{1,2})?/);
                    if (match) return match[0].trim();
                    return '';
                }

                function extractFromCard(el) {
                    const item = {};
                    item.name = extractName(el).substring(0, 250);

                    // URL
                    const link = el.querySelector('a[href]');
                    if (link) item.url = resolveUrl(link.getAttribute('href'));

                    // Price
                    item.price = extractPrice(el);

                    // Original price (strikethrough)
                    const del = el.querySelector('del, s, [class*="original"], [class*="old-price"], [class*="before"]');
                    if (del) item.original_price = (del.innerText || '').trim();

                    // Discount
                    const discountMatch = (el.innerText || '').match(/-?\\d{1,3}%/);
                    if (discountMatch) item.discount = discountMatch[0];

                    // Image
                    const img = el.querySelector('img');
                    if (img) item.image_url = resolveImgUrl(img);

                    // Rating
                    const ratingEl = el.querySelector('[class*="rating"],[class*="star"],[aria-label*="star"],[aria-label*="rating"]');
                    if (ratingEl) {
                        const rt = ratingEl.getAttribute('aria-label') || ratingEl.innerText || '';
                        const rm = rt.match(/(\\d+\\.?\\d*)/);
                        if (rm) item.rating = rm[1];
                    }

                    // Reviews/sold
                    const reviewMatch = (el.innerText || '').match(/(\\d[\\d,.]*\\s*(?:k|K)?)\\s*(?:review|rating|sold|revi)/i);
                    if (reviewMatch) item.reviews = reviewMatch[1];

                    return item;
                }

                // =============================================================
                // STRATEGY 1: JSON-LD / Schema.org structured data
                // =============================================================
                function tryJsonLd() {
                    const items = [];
                    for (const script of document.querySelectorAll('script[type="application/ld+json"]')) {
                        try {
                            let data = JSON.parse(script.textContent);
                            // Handle @graph wrapper
                            if (data['@graph']) data = data['@graph'];
                            // Normalize to array
                            const arr = Array.isArray(data) ? data : [data];
                            for (const obj of arr) {
                                // ItemList with ListItems
                                if (obj['@type'] === 'ItemList' && obj.itemListElement) {
                                    for (const li of obj.itemListElement) {
                                        const prod = li.item || li;
                                        if (prod.name) items.push(schemaToItem(prod));
                                    }
                                }
                                // Direct Product
                                if (obj['@type'] === 'Product' && obj.name) {
                                    items.push(schemaToItem(obj));
                                }
                                // Array of products
                                if (Array.isArray(obj)) {
                                    for (const p of obj) {
                                        if (p['@type'] === 'Product' && p.name) items.push(schemaToItem(p));
                                    }
                                }
                            }
                        } catch(e) {}
                    }
                    return items.length >= 2 ? items : null;
                }

                function schemaToItem(prod) {
                    const item = { name: prod.name || '' };
                    if (prod.url) item.url = resolveUrl(prod.url);
                    if (prod.image) {
                        const imgs = Array.isArray(prod.image) ? prod.image : [prod.image];
                        item.image_url = typeof imgs[0] === 'string' ? resolveUrl(imgs[0]) :
                                         imgs[0]?.url ? resolveUrl(imgs[0].url) : '';
                    }
                    const offers = prod.offers;
                    if (offers) {
                        const offer = offers.offers ? offers.offers[0] : offers;
                        if (offer) {
                            const p = offer.price || offer.lowPrice || '';
                            const c = offer.priceCurrency || '';
                            item.price = p ? (c + ' ' + p).trim() : '';
                        }
                    }
                    if (prod.aggregateRating) {
                        item.rating = prod.aggregateRating.ratingValue || '';
                        item.reviews = prod.aggregateRating.reviewCount || prod.aggregateRating.ratingCount || '';
                    }
                    if (prod.description) item.specs = (prod.description || '').substring(0, 300);
                    if (prod.brand) item.brand = typeof prod.brand === 'string' ? prod.brand : prod.brand.name || '';
                    return item;
                }

                // =============================================================
                // STRATEGY 2: Price-anchored DOM walking
                // Find all visible price elements, walk up to parent card, extract.
                // =============================================================
                function tryPriceAnchored() {
                    // Find small elements containing prices
                    function hasPrice(text) {
                        // Match known currency symbols/codes followed by digits
                        if (/(?:Rs\.\s*|Rs\s+|NPR\s*|\$|USD\s*|EUR\s*|€|£|¥|₹|৳|kr\s*|R\s)[\d,]+/.test(text)) return true;
                        if (/[\d,]+(?:\.\d{1,2})?\s*(?:Rs\.|NPR|USD|EUR|GBP|KRW|AUD|CAD)/.test(text)) return true;
                        // Catch-all: any non-ASCII currency symbol (₨, ₱, ₫, ₦, etc.) followed by digits
                        if (/[^\x00-\x7F][\s]*[\d,]+\.?\d{0,2}/.test(text) && text.length < 30) return true;
                        return false;
                    }
                    const priceEls = [];
                    const allEls = document.querySelectorAll('span, div, p, strong, b, em, ins, del, s, td, bdi, .woocommerce-Price-amount, .price');
                    for (const el of allEls) {
                        if (priceEls.length >= 200) break;
                        const text = (el.innerText || el.textContent || '').trim();
                        if (text.length >= 4 && text.length <= 60 && hasPrice(text)) {
                            priceEls.push(el);
                        }
                    }

                    if (priceEls.length < 2) return null;

                    // For each price element, walk up to find the product card ancestor
                    // Heuristic: card is the nearest ancestor that contains BOTH a link and an image
                    const seen = new Set();
                    const cards = [];
                    for (const priceEl of priceEls) {
                        let card = priceEl;
                        let bestCard = null;
                        for (let i = 0; i < 8; i++) {
                            card = card.parentElement;
                            if (!card || card === document.body) break;
                            // Stop if card is too large (sidebar, main content area)
                            const textLen = (card.innerText || '').length;
                            if (textLen > 1500) break;
                            const hasLink = !!card.querySelector('a[href]');
                            const hasImg = !!card.querySelector('img');
                            if (hasLink && hasImg) { bestCard = card; break; }
                            if (hasLink || hasImg) bestCard = card; // partial match, keep looking
                        }
                        const finalCard = bestCard || card;
                        if (finalCard && finalCard !== document.body && !seen.has(finalCard)) {
                            // Skip sidebar/nav/filter containers
                            const isNav = finalCard.closest('nav, aside, [role="navigation"], [class*="sidebar"], [class*="filter"], [class*="facet"]');
                            const textLen = (finalCard.innerText || '').length;
                            if (!isNav && textLen > 10 && textLen < 1500) {
                                seen.add(finalCard);
                                cards.push(finalCard);
                            }
                        }
                    }

                    if (cards.length < 2) return null;

                    const items = [];
                    for (let i = 0; i < Math.min(cards.length, MAX_ITEMS); i++) {
                        const data = extractFromCard(cards[i]);
                        if (data.name || data.price) items.push(data);
                    }
                    return items.length >= 2 ? items : null;
                }

                // =============================================================
                // STRATEGY 3: Card container detection (original, with scoring)
                // =============================================================
                function tryCardDetection() {
                    const candidates = document.querySelectorAll(
                        '[data-qa-locator="product-item"], [data-tracking], ' +
                        '[data-component="search"], [data-component="product"], ' +
                        '[class*="product-card"], [class*="product-item"], [class*="productCard"], ' +
                        '[class*="search-product"], [class*="s-result-item"], ' +
                        '[class*="product"], [class*="card"], [class*="listing"], [class*="result"], ' +
                        'ul > li, ol > li, [role="list"] > [role="listitem"]'
                    );

                    const groups = new Map();
                    for (const el of candidates) {
                        const parent = el.parentElement;
                        if (!parent) continue;
                        const sig = parent.tagName + '|' + el.tagName + '|' +
                            (el.className || '').toString().trim().split(/\\s+/).sort().join(' ');
                        if (!groups.has(sig)) groups.set(sig, []);
                        groups.get(sig).push(el);
                    }

                    function scoreGroup(els) {
                        if (els.length < 3) return -1;
                        let priceHits = 0, imgHits = 0;
                        const sample = els.slice(0, Math.min(els.length, 8));
                        for (const el of sample) {
                            if (/(?:Rs\.|Rs\s|\$|€|£|¥|₹|\u20A8|৳|NPR|USD|EUR)[\d,]+/.test(el.innerText || '')) priceHits++;
                            if (el.querySelector('img')) imgHits++;
                        }
                        const pp = priceHits / sample.length;
                        const ip = imgHits / sample.length;
                        return els.length + (pp * els.length * 10) + (ip * els.length * 3);
                    }

                    let bestGroup = [], bestScore = -1;
                    for (const [, els] of groups) {
                        const s = scoreGroup(els);
                        if (s > bestScore) { bestScore = s; bestGroup = els; }
                    }

                    if (bestGroup.length < 2) return null;

                    const items = [];
                    for (let i = 0; i < Math.min(bestGroup.length, MAX_ITEMS); i++) {
                        const data = extractFromCard(bestGroup[i]);
                        if (data.name || data.price) items.push(data);
                    }
                    return items.length >= 2 ? items : null;
                }

                // =============================================================
                // RUN STRATEGIES in priority order
                // =============================================================
                let items = null;
                let strategy = '';

                // Strategy 1: JSON-LD (highest quality when available)
                // Only use if it returns items WITH prices (some sites have JSON-LD without prices)
                try { items = tryJsonLd(); } catch(e) {}
                if (items && items.length >= 2) {
                    const withPrices = items.filter(i => i.price && i.price.length > 0).length;
                    if (withPrices >= items.length * 0.5) {
                        strategy = 'json-ld';
                    } else {
                        items = null; // Fall through — JSON-LD has no prices
                    }
                }

                // Strategy 2: Price-anchored (most universal for e-commerce)
                if (!items) {
                    try { items = tryPriceAnchored(); } catch(e) {}
                    if (items && items.length >= 2) strategy = 'price-anchored';
                    else items = null;
                }

                // Strategy 3: Card detection (fallback)
                if (!items) {
                    try { items = tryCardDetection(); } catch(e) {}
                    if (items && items.length >= 2) strategy = 'card-detection';
                    else items = null;
                }

                if (!items || items.length === 0) {
                    return JSON.stringify({error: "No listing structure detected. Try read_page or visual_check instead.", strategies_tried: ['json-ld', 'price-anchored', 'card-detection']});
                }

                // Post-filter: remove obvious non-product items
                const navWords = /^(price|filter|category|sort|shop on|popular|brand|condition|format|type|color|see all|show more|view all|sponsored)$/i;
                items = items.filter(item => {
                    const name = (item.name || '').trim();
                    // Too short to be a real product name
                    if (name.length > 0 && name.length < 8 && !item.specs) return false;
                    // Looks like a nav/filter label
                    if (navWords.test(name)) return false;
                    // Duplicate URLs (keep first)
                    return true;
                });
                // Deduplicate by URL
                const seenUrls = new Set();
                items = items.filter(item => {
                    if (!item.url) return true;
                    if (seenUrls.has(item.url)) return false;
                    seenUrls.add(item.url);
                    return true;
                });

                return JSON.stringify({
                    strategy: strategy,
                    total_items: items.length,
                    page_url: window.location.href,
                    items: items
                }, null, 2);
            }""")
            return ActionResult(
                action_id=action.action_id,
                status=ActionStatus.SUCCESS,
                message=f"Extracted {text[:50]}..." if len(text) > 50 else f"Extracted listings data",
                extracted_data=text,
            )

        # read_page tool sends value="__READ_PAGE__" — extract visible text
        elif action.value == "__READ_PAGE__":
            # Wait for DOM stability — JS-heavy sites (React/Vue) render content
            # after initial page load. Poll until innerText stops growing.
            text = await page.evaluate("""async () => {
                // Wait for DOM to stabilize (content stops changing)
                let prevLen = 0;
                let stableCount = 0;
                for (let i = 0; i < 10; i++) {
                    const curLen = (document.body.innerText || '').length;
                    if (curLen === prevLen && curLen > 0) {
                        stableCount++;
                        if (stableCount >= 2) break;  // Stable for 1 second
                    } else {
                        stableCount = 0;
                    }
                    prevLen = curLen;
                    await new Promise(r => setTimeout(r, 500));
                }

                // Now extract content
                const contentSelectors = [
                    'main', 'article', '[role="main"]',
                    '#content', '#main-content', '.content', '.main',
                    '#readme', '.markdown-body', '.entry-content',
                ];
                let contentEl = null;
                for (const sel of contentSelectors) {
                    const el = document.querySelector(sel);
                    if (el && el.innerText && el.innerText.trim().length > 50) {
                        contentEl = el;
                        break;
                    }
                }

                const source = contentEl || document.body;
                const fullText = (source.innerText || '').trim();

                // Use scroll position to return different content after scrolling
                const viewH = window.innerHeight;
                const scrollY = window.scrollY;
                const totalHeight = document.documentElement.scrollHeight;
                const scrollRatio = totalHeight > viewH ? scrollY / (totalHeight - viewH) : 0;

                const startPos = Math.floor(scrollRatio * Math.max(0, fullText.length - 4000));
                return fullText.substring(startPos, startPos + 4000);
            }""")
        elif action.element_id is not None:
            selector = _find_element_selector(page_context, action.element_id)
            if selector:
                text = await page.locator(selector).first.inner_text(timeout=timeout_ms)
            else:
                text = ""
        else:
            text = await page.inner_text("body")
        text = text[:2000]  # Limit to prevent bloat
        return ActionResult(
            action_id=action.action_id,
            status=ActionStatus.SUCCESS,
            message=f"Extracted {len(text)} chars of text",
            extracted_data=text,
        )

    elif at == ActionType.TAKE_SCREENSHOT:
        screenshot = await page.screenshot(type="png")

        # Check if this is a visual_check (screenshot + vision analysis)
        if action.value and action.value.startswith("__VISUAL_CHECK__"):
            query = action.value.replace("__VISUAL_CHECK__|", "")
            vision_result = await _analyze_screenshot_with_vision(screenshot, query, page.url)
            return ActionResult(
                action_id=action.action_id,
                status=ActionStatus.SUCCESS,
                message=f"Visual analysis complete",
                extracted_data=vision_result,
            )

        return ActionResult(
            action_id=action.action_id,
            status=ActionStatus.SUCCESS,
            message=f"Screenshot taken ({len(screenshot)} bytes)",
        )

    # --- Tab management ---
    elif at == ActionType.NEW_TAB:
        url = action.value or "about:blank"
        new_page = await page.context.new_page()
        if url and url != "about:blank":
            await new_page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
        return ActionResult(
            action_id=action.action_id,
            status=ActionStatus.SUCCESS,
            message=f"Opened new tab: {url}",
            page_changed=True,
            new_url=new_page.url,
        )

    elif at == ActionType.CLOSE_TAB:
        pages = page.context.pages
        if len(pages) > 1:
            await page.close()
            return ActionResult(
                action_id=action.action_id,
                status=ActionStatus.SUCCESS,
                message="Closed current tab",
                page_changed=True,
            )
        return ActionResult(
            action_id=action.action_id,
            status=ActionStatus.FAILED,
            message="Cannot close the last tab",
            error="Only one tab open",
        )

    elif at == ActionType.SWITCH_TAB:
        tab_index = int(action.value or "0")
        pages = page.context.pages
        if 0 <= tab_index < len(pages):
            target_page = pages[tab_index]
            await target_page.bring_to_front()
            return ActionResult(
                action_id=action.action_id,
                status=ActionStatus.SUCCESS,
                message=f"Switched to tab {tab_index}",
                page_changed=True,
                new_url=target_page.url,
            )
        return ActionResult(
            action_id=action.action_id,
            status=ActionStatus.FAILED,
            message=f"Tab index {tab_index} out of range (have {len(pages)} tabs)",
            error="Invalid tab index",
        )

    # --- Information gathering (additional) ---
    elif at == ActionType.EXTRACT_TABLE:
        if action.element_id is not None:
            selector = _find_element_selector(page_context, action.element_id)
            if selector:
                table_data = await page.locator(selector).first.evaluate("""
                    (el) => {
                        const rows = [];
                        for (const tr of el.querySelectorAll('tr')) {
                            const cells = [];
                            for (const td of tr.querySelectorAll('td, th')) {
                                cells.push(td.innerText.trim());
                            }
                            if (cells.length > 0) rows.push(cells);
                        }
                        return JSON.stringify(rows);
                    }
                """, timeout=timeout_ms)
                return ActionResult(
                    action_id=action.action_id,
                    status=ActionStatus.SUCCESS,
                    message=f"Extracted table data",
                    extracted_data=table_data,
                )
        return ActionResult(
            action_id=action.action_id,
            status=ActionStatus.FAILED,
            message="Could not find table element",
            error="Missing or invalid element_id",
        )

    elif at == ActionType.GET_CONSOLE_LOGS:
        # Note: console logs must be captured via event listeners set up beforehand
        return ActionResult(
            action_id=action.action_id,
            status=ActionStatus.SUCCESS,
            message="Console log capture not yet implemented in Playwright mode",
            extracted_data="[]",
        )

    elif at == ActionType.GET_NETWORK_LOG:
        return ActionResult(
            action_id=action.action_id,
            status=ActionStatus.SUCCESS,
            message="Network log capture not yet implemented in Playwright mode",
            extracted_data="[]",
        )

    # --- JavaScript execution ---
    elif at == ActionType.EVALUATE_JS:
        code = action.value or ""
        try:
            result_val = await page.evaluate(code)
            result_str = str(result_val) if result_val is not None else "undefined"
            return ActionResult(
                action_id=action.action_id,
                status=ActionStatus.SUCCESS,
                message=f"JS evaluation returned: {result_str[:200]}",
                extracted_data=result_str[:2000],
            )
        except Exception as e:
            return ActionResult(
                action_id=action.action_id,
                status=ActionStatus.FAILED,
                message=f"JS evaluation failed: {str(e)[:200]}",
                error=str(e),
            )

    # --- Dialog handling ---
    elif at == ActionType.HANDLE_DIALOG:
        # Dialogs are usually handled via page.on("dialog") event listener
        # This is a fallback that accepts any pending dialog
        return ActionResult(
            action_id=action.action_id,
            status=ActionStatus.SUCCESS,
            message=f"Dialog action: {action.value or 'accept'}",
        )

    # --- File upload ---
    elif at == ActionType.UPLOAD_FILE:
        if action.element_id is not None:
            selector = _find_element_selector(page_context, action.element_id)
            if selector:
                await page.locator(selector).first.set_input_files(
                    action.value or "", timeout=timeout_ms
                )
                return ActionResult(
                    action_id=action.action_id,
                    status=ActionStatus.SUCCESS,
                    message=f"Uploaded file: {action.value}",
                )
        return ActionResult(
            action_id=action.action_id,
            status=ActionStatus.FAILED,
            message="Could not find file input element",
            error="Missing or invalid element_id",
        )

    # --- Drag ---
    elif at == ActionType.DRAG:
        source_selector = _find_element_selector(page_context, action.element_id)
        target_id = int(action.value or "0")
        target_selector = _find_element_selector(page_context, target_id)
        if source_selector and target_selector:
            await page.locator(source_selector).first.drag_to(
                page.locator(target_selector).first, timeout=timeout_ms
            )
            return ActionResult(
                action_id=action.action_id,
                status=ActionStatus.SUCCESS,
                message=f"Dragged element {action.element_id} to element {target_id}",
            )
        return ActionResult(
            action_id=action.action_id,
            status=ActionStatus.FAILED,
            message="Could not find source or target element for drag",
            error="Missing selectors",
        )

    # --- Smart waiting ---
    elif at == ActionType.WAIT_FOR_SELECTOR:
        parts = (action.value or "").split("|")
        css_sel = parts[0] if parts else ""
        wait_timeout = float(parts[1]) * 1000 if len(parts) > 1 else 10000
        try:
            await page.wait_for_selector(css_sel, timeout=int(wait_timeout))
            return ActionResult(
                action_id=action.action_id,
                status=ActionStatus.SUCCESS,
                message=f"Selector '{css_sel}' appeared",
            )
        except PlaywrightTimeout:
            return ActionResult(
                action_id=action.action_id,
                status=ActionStatus.TIMEOUT,
                message=f"Selector '{css_sel}' did not appear within {wait_timeout/1000}s",
                error="Wait for selector timed out",
            )

    elif at == ActionType.WAIT_FOR_NAVIGATION:
        wait_timeout = float(action.value or "10") * 1000
        try:
            await page.wait_for_url("**", timeout=int(wait_timeout))
            return ActionResult(
                action_id=action.action_id,
                status=ActionStatus.SUCCESS,
                message=f"Navigation completed: {page.url}",
                page_changed=True,
                new_url=page.url,
            )
        except PlaywrightTimeout:
            return ActionResult(
                action_id=action.action_id,
                status=ActionStatus.TIMEOUT,
                message="Navigation did not occur within timeout",
                error="Wait for navigation timed out",
            )

    # --- Wait ---
    elif at == ActionType.WAIT:
        try:
            seconds = float(action.value or "2")
        except (ValueError, TypeError):
            seconds = 2.0  # Default if value is a description string, not a number
        seconds = min(seconds, 10)  # Cap at 10 seconds
        await page.wait_for_timeout(int(seconds * 1000))
        return ActionResult(
            action_id=action.action_id,
            status=ActionStatus.SUCCESS,
            message=f"Waited {seconds}s",
        )

    # --- Done ---
    elif at == ActionType.DONE:
        return ActionResult(
            action_id=action.action_id,
            status=ActionStatus.SUCCESS,
            message="Agent marked task as done",
        )

    # --- Fallback ---
    return ActionResult(
        action_id=action.action_id,
        status=ActionStatus.FAILED,
        message=f"Unsupported action type: {at.value}",
        error=f"No handler for {at.value}",
    )
