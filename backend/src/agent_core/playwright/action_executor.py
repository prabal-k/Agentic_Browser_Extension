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
        ActionType.CLICK, ActionType.TYPE_TEXT, ActionType.CLEAR_AND_TYPE,
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
            await locator.click(timeout=timeout_ms)
            return ActionResult(
                action_id=action.action_id,
                status=ActionStatus.SUCCESS,
                message=f"Clicked element {action.element_id}",
            )

        elif at == ActionType.TYPE_TEXT:
            raw_value = action.value or ""
            should_submit = raw_value.endswith("|SUBMIT")
            text = raw_value.replace("|SUBMIT", "") if should_submit else raw_value

            # Try fill() first (fastest), fall back to press_sequentially (works on SPAs)
            try:
                await locator.fill(text, timeout=timeout_ms)
            except Exception:
                # fill() failed — element might be a custom component
                # Fall back to clicking + typing character by character
                await locator.click(timeout=timeout_ms)
                await page.keyboard.type(text, delay=30)

            if should_submit:
                await page.wait_for_timeout(100)
                await page.keyboard.press("Enter")

            return ActionResult(
                action_id=action.action_id,
                status=ActionStatus.SUCCESS,
                message=f"Typed '{text}' into element {action.element_id}{' and submitted' if should_submit else ''}",
            )

        elif at == ActionType.CLEAR_AND_TYPE:
            raw_value = action.value or ""
            should_submit = raw_value.endswith("|SUBMIT")
            text = raw_value.replace("|SUBMIT", "") if should_submit else raw_value

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
        # read_page tool sends value="__READ_PAGE__" — extract visible text
        if action.value == "__READ_PAGE__":
            text = await page.evaluate("""() => {
                // Simple and reliable: use document.body.innerText which
                // automatically excludes scripts/styles and respects visibility.
                // Then trim to reasonable size.
                const fullText = (document.body.innerText || '').trim();
                // Skip the first ~200 chars (usually nav/header) and get the meat
                const skipHeader = fullText.length > 500 ? fullText.substring(200) : fullText;
                return skipHeader.substring(0, 4000);
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
