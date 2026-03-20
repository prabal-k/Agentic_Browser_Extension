"""Playwright orchestrator — Connects real browser to the WebSocket agent server.

This is the full integration loop:
1. Opens a Chromium browser with Playwright
2. Connects to the FastAPI WebSocket server
3. Navigates to a target URL
4. Extracts live DOM → sends to agent as goal context
5. Receives actions from agent → executes on real page
6. Extracts new DOM → sends back → agent evaluates → next action
7. Repeats until done or max iterations

Usage:
    python -m agent_core.playwright.orchestrator --url "https://example.com" --goal "Find the search box"
"""

import asyncio
import json
import time
import argparse
import structlog

from playwright.async_api import async_playwright, Browser, Page

from agent_core.playwright.dom_extractor import extract_page_context
from agent_core.playwright.action_executor import execute_action
from agent_core.schemas.actions import Action, ActionType
from agent_core.schemas.dom import PageContext
from agent_core.config import settings

logger = structlog.get_logger("playwright.orchestrator")


import builtins as _builtins


def _safe_print(msg: str) -> None:
    """Print with safe encoding — replaces non-ASCII chars to prevent Windows encoding errors."""
    try:
        _builtins.print(msg)
    except UnicodeEncodeError:
        _builtins.print(msg.encode('ascii', 'replace').decode('ascii'))


try:
    import websockets
except ImportError:
    websockets = None  # type: ignore


class OrchestratorResult:
    """Result of a full orchestrator run."""

    def __init__(self):
        self.success: bool = False
        self.summary: str = ""
        self.actions_executed: int = 0
        self.iterations: int = 0
        self.total_time_s: float = 0.0
        self.errors: list[str] = []
        self.action_log: list[dict] = []


async def run_scenario(
    url: str,
    goal: str,
    ws_url: str = "ws://localhost:8000/ws",
    headless: bool = False,
    timeout_s: int = 300,
    slow_mo: int = 500,
) -> OrchestratorResult:
    """Run a full agent scenario on a real website.

    Args:
        url: Starting URL to navigate to
        goal: Goal text for the agent
        ws_url: WebSocket server URL
        headless: Run browser headlessly (no visible window)
        timeout_s: Max total time for the scenario
        slow_mo: Milliseconds to slow down Playwright actions (for visibility)

    Returns:
        OrchestratorResult with success/failure info and action log
    """
    if websockets is None:
        raise ImportError("websockets package required: pip install websockets")

    result = OrchestratorResult()
    start_time = time.time()

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=headless, slow_mo=slow_mo)
        context = await browser.new_context(viewport={"width": 1280, "height": 720})

        # Enable tracing for debugging
        await context.tracing.start(screenshots=True, snapshots=True)
        page = await context.new_page()

        try:
            # Navigate to starting URL
            _safe_print(f"\n{'='*60}")
            _safe_print(f"  Scenario: {goal}")
            _safe_print(f"  URL: {url}")
            _safe_print(f"{'='*60}\n")

            await page.goto(url, wait_until="domcontentloaded", timeout=15000)
            await page.wait_for_timeout(1000)  # Let page settle

            # Extract initial DOM
            _safe_print("[1] Extracting initial DOM...")
            page_context = await extract_page_context(page)
            safe_title = page_context.title.encode('ascii', 'replace').decode('ascii')
            _safe_print(f"    Found {len(page_context.elements)} elements on {safe_title}")

            # Connect to WebSocket server
            _safe_print(f"[2] Connecting to {ws_url}...")
            async with websockets.connect(ws_url) as ws:
                # Read initial status
                init_msg = json.loads(await ws.recv())
                session_id = init_msg.get("session_id", "?")
                _safe_print(f"    Connected! Session: {session_id}")

                # Send goal + DOM
                _safe_print(f"[3] Sending goal: {goal}")
                await ws.send(json.dumps({
                    "type": "client_goal",
                    "goal": goal,
                    "dom_snapshot": page_context.model_dump(),
                }))

                # Main loop: receive messages, handle actions/interrupts
                _safe_print(f"[4] Running agent loop...\n")
                done = False

                while not done and (time.time() - start_time) < timeout_s:
                    try:
                        raw = await asyncio.wait_for(ws.recv(), timeout=120)
                    except asyncio.TimeoutError:
                        result.errors.append("WebSocket receive timeout (120s)")
                        break

                    msg = json.loads(raw)
                    msg_type = msg.get("type", "")

                    if msg_type == "server_status":
                        status = msg.get("cognitive_status", "")
                        _safe_print(f"    STATUS: {status}")

                    elif msg_type == "server_reasoning":
                        content = msg.get("content", "")[:150]
                        safe_content = content.encode('ascii', 'replace').decode('ascii')
                        _safe_print(f"    REASONING: {safe_content}...")

                    elif msg_type == "server_plan":
                        steps = msg.get("steps", [])
                        _safe_print(f"    PLAN (v{msg.get('plan_version', '?')}, {len(steps)} steps):")
                        for s in steps[:5]:
                            desc = s.get("description", s) if isinstance(s, dict) else str(s)
                            _safe_print(f"      - {desc}")

                    elif msg_type == "server_action_request":
                        action_data = msg.get("action", {})
                        action_type = action_data.get("action_type", "?")
                        element_id = action_data.get("element_id")
                        value = action_data.get("value", "")
                        confidence = action_data.get("confidence", 0)

                        _safe_print(f"    ACTION: {action_type} on element [{element_id}] "
                              f"(confidence: {confidence:.0%})")

                        if msg.get("execute"):
                            # Build Action object and execute
                            try:
                                action = Action(
                                    action_id=action_data.get("action_id", ""),
                                    action_type=ActionType(action_type),
                                    element_id=element_id,
                                    value=value,
                                    description=action_data.get("description", ""),
                                    confidence=confidence,
                                )
                            except ValueError as e:
                                result.errors.append(f"Invalid action: {e}")
                                await ws.send(json.dumps({
                                    "type": "client_action_result",
                                    "action_result": {
                                        "status": "failed",
                                        "message": f"Invalid action type: {action_type}",
                                    },
                                }))
                                continue

                            _safe_print(f"    EXECUTING: {action.action_type.value}...")
                            action_result = await execute_action(
                                page, action, page_context
                            )
                            result.actions_executed += 1

                            status_str = action_result.status.value
                            _safe_print(f"    RESULT: {status_str} - {action_result.message}")

                            result.action_log.append({
                                "action": action_type,
                                "element_id": element_id,
                                "status": status_str,
                                "message": action_result.message,
                                "time_ms": action_result.execution_time_ms,
                            })

                            # Non-mutating actions: skip DOM re-extraction (use cached context)
                            # This saves 200-500ms per action on complex pages
                            _NON_MUTATING = {'scroll_down', 'scroll_up', 'extract_text',
                                             'wait', 'take_screenshot', 'get_console_logs',
                                             'get_network_log', 'wait_for_selector',
                                             'wait_for_navigation'}

                            skip_dom = (
                                action_type in _NON_MUTATING
                                and not action_result.page_changed
                            )

                            if not skip_dom:
                                # Wait for page to settle after action
                                wait_time = 2000 if action_result.page_changed else 800
                                await page.wait_for_timeout(wait_time)

                                # Extract new DOM (with retry for navigation timing)
                                try:
                                    page_context = await extract_page_context(page)
                                except Exception:
                                    await page.wait_for_timeout(2000)
                                    try:
                                        page_context = await extract_page_context(page)
                                    except Exception as dom_err:
                                        _safe_print(f"    DOM extraction failed: {str(dom_err)[:80]}")
                                        continue

                                safe_url = page_context.url.encode('ascii', 'replace').decode('ascii')
                                _safe_print(f"    NEW DOM: {len(page_context.elements)} elements "
                                      f"on {safe_url}")
                            else:
                                _safe_print(f"    CACHED DOM (non-mutating action: {action_type})")

                            # Send result + new DOM back (include extracted_data for read_page/visual_check)
                            action_result_dict = {
                                "status": status_str,
                                "message": action_result.message,
                                "page_changed": action_result.page_changed,
                                "new_url": action_result.new_url,
                                "execution_time_ms": action_result.execution_time_ms,
                            }
                            if action_result.extracted_data:
                                action_result_dict["extracted_data"] = action_result.extracted_data

                            ws_payload = {
                                "type": "client_action_result",
                                "action_result": action_result_dict,
                            }
                            if not skip_dom:
                                ws_payload["new_dom_snapshot"] = page_context.model_dump()

                            await ws.send(json.dumps(ws_payload))

                    elif msg_type == "server_interrupt":
                        title = msg.get("title", "")
                        context_text = msg.get("context", "")
                        fields = msg.get("fields", [])
                        _safe_print(f"    INTERRUPT: {title} - {context_text[:80]}")

                        # Auto-respond to interrupts
                        values: dict = {}
                        for field in fields:
                            fid = field.get("field_id", "")
                            ftype = field.get("field_type", "")
                            if ftype == "confirm" or fid == "confirmed":
                                values["confirmed"] = True
                            elif ftype == "text" or fid == "answer":
                                values["answer"] = "Yes, proceed with the task"
                            else:
                                values[fid] = "yes"

                        _safe_print(f"    AUTO-CONFIRM: {values}")
                        await ws.send(json.dumps({
                            "type": "client_user_response",
                            "values": values,
                        }))

                    elif msg_type == "server_evaluation":
                        progress = msg.get("progress_percentage", 0)
                        succeeded = msg.get("action_succeeded", False)
                        summary = msg.get("summary", "")
                        result.iterations += 1
                        _safe_print(f"    EVAL: {'OK' if succeeded else 'FAIL'} "
                              f"- progress {progress}% - {summary[:80]}")

                    elif msg_type == "server_done":
                        result.success = msg.get("success", False)
                        result.summary = msg.get("summary", "")
                        _safe_print(f"\n{'='*60}")
                        _safe_print(f"  DONE: {'SUCCESS' if result.success else 'FAILED'}")
                        _safe_print(f"  Summary: {result.summary}")
                        _safe_print(f"{'='*60}")
                        done = True

                    elif msg_type == "server_error":
                        error_msg = msg.get("message", "Unknown error")
                        recoverable = msg.get("recoverable", False)
                        _safe_print(f"    ERROR: {error_msg[:150]}")
                        result.errors.append(error_msg)
                        if not recoverable:
                            done = True

                    else:
                        _safe_print(f"    [{msg_type}]: {json.dumps(msg)[:100]}")

        except Exception as e:
            result.errors.append(str(e))
            logger.error("orchestrator_error", error=str(e))
            _safe_print(f"\n  FATAL ERROR: {e}")

        finally:
            result.total_time_s = time.time() - start_time

            # Save trace
            trace_path = f"trace_{int(time.time())}.zip"
            try:
                await context.tracing.stop(path=trace_path)
                _safe_print(f"\n  Trace saved: {trace_path}")
                _safe_print(f"  View with: npx playwright show-trace {trace_path}")
            except Exception:
                pass

            await browser.close()

    # Print summary
    print(f"\n{'='*60}")
    print(f"  SUMMARY")
    print(f"  Actions executed: {result.actions_executed}")
    print(f"  Iterations: {result.iterations}")
    print(f"  Total time: {result.total_time_s:.1f}s")
    print(f"  Errors: {len(result.errors)}")
    if result.errors:
        for err in result.errors[:5]:
            _safe_print(f"    - {err[:100]}")
    print(f"{'='*60}\n")

    return result


# --- Test Scenarios ---

SCENARIOS = [
    {
        "name": "Wikipedia Search",
        "url": "https://en.wikipedia.org",
        "goal": "Search for 'Python programming language' using the search box",
    },
    {
        "name": "Google Search",
        "url": "https://www.google.com",
        "goal": "Search for 'what is machine learning' using the search box",
    },
    {
        "name": "Hacker News Navigation",
        "url": "https://news.ycombinator.com",
        "goal": "Click on the 'new' link in the top navigation bar",
    },
    {
        "name": "DuckDuckGo Search",
        "url": "https://duckduckgo.com",
        "goal": "Type 'playwright browser automation' in the search box and press Enter",
    },
    {
        "name": "Example.com Info",
        "url": "https://example.com",
        "goal": "Extract the main heading text and the first link on the page",
    },
]


async def run_all_scenarios(
    ws_url: str = "ws://localhost:8000/ws",
    headless: bool = False,
) -> dict:
    """Run all predefined test scenarios and produce a report."""
    results = {}

    for scenario in SCENARIOS:
        name = scenario["name"]
        _safe_print(f"\n\n{'#'*60}")
        _safe_print(f"  SCENARIO: {name}")
        _safe_print(f"{'#'*60}")

        result = await run_scenario(
            url=scenario["url"],
            goal=scenario["goal"],
            ws_url=ws_url,
            headless=headless,
        )
        results[name] = {
            "success": result.success,
            "actions": result.actions_executed,
            "iterations": result.iterations,
            "time_s": round(result.total_time_s, 1),
            "errors": result.errors,
        }

        # Brief pause between scenarios
        await asyncio.sleep(2)

    # Print final report
    print(f"\n\n{'='*60}")
    print(f"  FINAL REPORT")
    print(f"{'='*60}")
    passed = sum(1 for r in results.values() if r["success"])
    total = len(results)
    print(f"  Passed: {passed}/{total}")
    print()
    for name, r in results.items():
        status = "PASS" if r["success"] else "FAIL"
        _safe_print(f"  [{status}] {name} - {r['actions']} actions, {r['time_s']}s")
        if r["errors"]:
            for err in r["errors"][:2]:
                _safe_print(f"         Error: {err[:80]}")
    print(f"{'='*60}\n")

    return results


def main():
    parser = argparse.ArgumentParser(description="Playwright Agent Orchestrator")
    parser.add_argument("--url", help="Target URL to test")
    parser.add_argument("--goal", help="Goal for the agent")
    parser.add_argument("--ws-url", default="ws://localhost:8000/ws", help="WebSocket server URL")
    parser.add_argument("--headless", action="store_true", help="Run browser headlessly")
    parser.add_argument("--all", action="store_true", help="Run all predefined scenarios")
    parser.add_argument("--slow-mo", type=int, default=500, help="Slow down actions (ms)")

    args = parser.parse_args()

    if args.all:
        asyncio.run(run_all_scenarios(ws_url=args.ws_url, headless=args.headless))
    elif args.url and args.goal:
        asyncio.run(run_scenario(
            url=args.url,
            goal=args.goal,
            ws_url=args.ws_url,
            headless=args.headless,
            slow_mo=args.slow_mo,
        ))
    else:
        parser.print_help()
        _safe_print("\nExamples:")
        _safe_print('  python -m agent_core.playwright.orchestrator --all')
        _safe_print('  python -m agent_core.playwright.orchestrator --url "https://en.wikipedia.org" --goal "Search for Python"')


if __name__ == "__main__":
    main()
