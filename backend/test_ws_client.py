"""Quick WebSocket test client for Phase 4 server.

Simulates a browser client that responds to agent actions with
progressively changing DOM states, so the agent sees progress.

Usage:
    1. Start the server:  python -m agent_core.server
    2. Run this script:   python test_ws_client.py
"""

import asyncio
import copy
import json
import time

try:
    import websockets
except ImportError:
    print("Installing websockets...")
    import subprocess, sys
    subprocess.check_call([sys.executable, "-m", "pip", "install", "websockets"])
    import websockets


# --- DOM Snapshots that simulate progressive browser state changes ---

INITIAL_DOM = {
    "url": "https://example.com",
    "title": "Example Page",
    "timestamp": time.time(),
    "viewport_width": 1920,
    "viewport_height": 1080,
    "scroll_position": 0.0,
    "has_more_content_below": False,
    "elements": [
        {
            "element_id": 1,
            "element_type": "text_input",
            "tag_name": "input",
            "text": "",
            "attributes": {"type": "text", "name": "q", "placeholder": "Search..."},
            "is_visible": True,
            "is_enabled": True,
            "is_focused": False,
            "bounding_box": {"x": 100, "y": 50, "width": 300, "height": 40},
            "parent_context": "inside form: Search",
            "css_selector": "input[name='q']",
        },
        {
            "element_id": 2,
            "element_type": "button",
            "tag_name": "button",
            "text": "Search",
            "attributes": {"type": "submit"},
            "is_visible": True,
            "is_enabled": True,
            "is_focused": False,
            "bounding_box": {"x": 410, "y": 50, "width": 80, "height": 40},
            "parent_context": "inside form: Search",
            "css_selector": "button[type='submit']",
        },
        {
            "element_id": 3,
            "element_type": "heading",
            "tag_name": "h1",
            "text": "Welcome to Example.com",
            "attributes": {},
            "is_visible": True,
            "is_enabled": True,
        },
    ],
    "forms": [
        {"name": "Search", "action": "/search", "method": "GET", "field_ids": [1, 2]}
    ],
}

# After typing in the search box — input now has value and is focused
AFTER_TYPE_DOM = {
    "url": "https://example.com",
    "title": "Example Page",
    "timestamp": time.time(),
    "viewport_width": 1920,
    "viewport_height": 1080,
    "scroll_position": 0.0,
    "has_more_content_below": False,
    "elements": [
        {
            "element_id": 1,
            "element_type": "text_input",
            "tag_name": "input",
            "text": "hello world",
            "attributes": {
                "type": "text",
                "name": "q",
                "placeholder": "Search...",
                "value": "hello world",
            },
            "is_visible": True,
            "is_enabled": True,
            "is_focused": True,
            "bounding_box": {"x": 100, "y": 50, "width": 300, "height": 40},
            "parent_context": "inside form: Search",
            "css_selector": "input[name='q']",
        },
        {
            "element_id": 2,
            "element_type": "button",
            "tag_name": "button",
            "text": "Search",
            "attributes": {"type": "submit"},
            "is_visible": True,
            "is_enabled": True,
            "is_focused": False,
            "bounding_box": {"x": 410, "y": 50, "width": 80, "height": 40},
            "parent_context": "inside form: Search",
            "css_selector": "button[type='submit']",
        },
        {
            "element_id": 3,
            "element_type": "heading",
            "tag_name": "h1",
            "text": "Welcome to Example.com",
            "attributes": {},
            "is_visible": True,
            "is_enabled": True,
        },
    ],
    "forms": [
        {"name": "Search", "action": "/search", "method": "GET", "field_ids": [1, 2]}
    ],
}

# After clicking search — navigated to results page
AFTER_SEARCH_DOM = {
    "url": "https://example.com/search?q=hello+world",
    "title": "Search Results - hello world",
    "timestamp": time.time(),
    "viewport_width": 1920,
    "viewport_height": 1080,
    "scroll_position": 0.0,
    "has_more_content_below": True,
    "page_text_summary": "Showing results for 'hello world'. 1. Hello World Program - Wikipedia. 2. Hello World in Python...",
    "elements": [
        {
            "element_id": 1,
            "element_type": "text_input",
            "tag_name": "input",
            "text": "hello world",
            "attributes": {
                "type": "text",
                "name": "q",
                "value": "hello world",
            },
            "is_visible": True,
            "is_enabled": True,
            "is_focused": False,
            "parent_context": "inside form: Search",
            "css_selector": "input[name='q']",
        },
        {
            "element_id": 10,
            "element_type": "link",
            "tag_name": "a",
            "text": "Hello World Program - Wikipedia",
            "attributes": {"href": "https://en.wikipedia.org/wiki/Hello_world"},
            "is_visible": True,
            "is_enabled": True,
            "parent_context": "search result 1",
        },
        {
            "element_id": 11,
            "element_type": "paragraph",
            "tag_name": "p",
            "text": "A 'Hello, World!' program is a computer program that outputs 'Hello, World!'",
            "attributes": {},
            "is_visible": True,
            "is_enabled": True,
            "parent_context": "search result 1",
        },
        {
            "element_id": 12,
            "element_type": "link",
            "tag_name": "a",
            "text": "Hello World in Every Programming Language",
            "attributes": {"href": "https://helloworld.example.com"},
            "is_visible": True,
            "is_enabled": True,
            "parent_context": "search result 2",
        },
    ],
}


class SimulatedBrowser:
    """Simulates DOM changes based on what action the agent performed."""

    def __init__(self):
        self.action_count = 0
        self.current_dom = copy.deepcopy(INITIAL_DOM)

    def execute_action(self, action_request: dict) -> dict:
        """Return an updated DOM based on the action type."""
        self.action_count += 1
        action = action_request.get("action", {})
        # Server sends action_type (from our schema), not "tool"
        action_type = action.get("action_type", action.get("tool", ""))
        element_id = action.get("element_id")
        value = action.get("value", "")

        print(f"     [Browser] Action #{self.action_count}: {action_type} on element {element_id}")

        if action_type in ("type_text", "fill", "set_value"):
            self.current_dom = copy.deepcopy(AFTER_TYPE_DOM)
            self.current_dom["timestamp"] = time.time()
            return {
                "success": True,
                "message": f"Typed '{value}' into element {element_id}",
            }

        elif action_type == "click":
            # If clicking search button after typing, show results
            if element_id == 2 and self.action_count >= 2:
                self.current_dom = copy.deepcopy(AFTER_SEARCH_DOM)
                self.current_dom["timestamp"] = time.time()
                return {
                    "success": True,
                    "message": f"Clicked element {element_id}, page navigated to search results",
                }
            else:
                return {
                    "success": True,
                    "message": f"Clicked element {element_id}",
                }

        else:
            # For any other action, just bump timestamp
            self.current_dom["timestamp"] = time.time()
            return {
                "success": True,
                "message": f"Executed {action_type} on element {element_id}",
            }


async def test():
    uri = "ws://localhost:8002/ws"
    print(f"Connecting to {uri} ...")
    print(f"Goal: Find the search box and type 'hello world'\n")

    browser = SimulatedBrowser()

    async with websockets.connect(uri) as ws:
        # 1) Read the initial server_status message
        msg = json.loads(await ws.recv())
        print(f"Connected! Session: {msg.get('session_id', 'unknown')}")
        print(f"  <- {msg['type']}: {msg.get('status', '')}\n")

        # 2) Send goal with initial DOM
        goal_msg = {
            "type": "client_goal",
            "goal": "Find the search box and type 'hello world'",
            "dom_snapshot": INITIAL_DOM,
        }

        print(f"  -> Sending goal\n")
        await ws.send(json.dumps(goal_msg))

        # 3) Listen and respond to server messages
        interrupt_count = 0
        max_interrupts = 30  # safety limit

        try:
            while True:
                raw = await asyncio.wait_for(ws.recv(), timeout=180)
                msg = json.loads(raw)
                msg_type = msg.get("type", "unknown")

                if msg_type == "server_status":
                    print(f"  <- STATUS: {msg.get('status', '')}")

                elif msg_type == "server_reasoning":
                    text = msg.get("reasoning", "")[:200]
                    print(f"  <- REASONING: {text}...")

                elif msg_type == "server_plan":
                    steps = msg.get("steps", [])
                    print(f"  <- PLAN ({len(steps)} steps):")
                    for s in steps[:5]:
                        if isinstance(s, dict):
                            print(f"     - {s.get('description', s)}")
                        else:
                            print(f"     - {s}")

                elif msg_type == "server_action_request":
                    action = msg.get("action", {})
                    action_type = action.get("action_type", action.get("tool", "?"))
                    elem_id = action.get("element_id", "?")
                    print(f"  <- ACTION REQUEST: {action_type} on element {elem_id}")

                    # Simulate browser execution with changing DOM
                    if msg.get("execute"):
                        result = browser.execute_action(msg)
                        # Match server's expected format: action_result + new_dom_snapshot
                        response = {
                            "type": "client_action_result",
                            "action_result": {
                                "status": "success" if result["success"] else "failed",
                                "message": result["message"],
                                "page_changed": True,
                                "execution_time_ms": 150,
                            },
                            "new_dom_snapshot": browser.current_dom,
                        }
                        print(f"  -> Sending action result: {result['message']}")
                        print(f"     [Browser] Current URL: {browser.current_dom['url']}")
                        await ws.send(json.dumps(response))

                elif msg_type == "server_interrupt":
                    interrupt_count += 1
                    fields = msg.get("fields", [])
                    title = msg.get("title", "")
                    context = msg.get("context", "")
                    print(f"  <- INTERRUPT #{interrupt_count}: {title} - {context[:80]}")

                    if interrupt_count > max_interrupts:
                        print(f"\n  !! Too many interrupts ({max_interrupts}), stopping.")
                        cancel = {"type": "client_cancel"}
                        await ws.send(json.dumps(cancel))
                        break

                    # Auto-respond using `values` dict matching server's expected format
                    values = {}
                    for field in fields:
                        fid = field.get("field_id", "")
                        ftype = field.get("field_type", "")
                        if ftype == "confirm" or fid == "confirmed":
                            values["confirmed"] = True
                        elif ftype == "text" or fid == "answer":
                            values["answer"] = "Yes, go ahead and complete the task"
                        elif fid == "response":
                            values["response"] = "Yes, proceed"
                        else:
                            values[fid] = "yes"

                    response = {
                        "type": "client_user_response",
                        "values": values,
                    }
                    print(f"  -> Auto-confirmed: {values}")
                    await ws.send(json.dumps(response))

                elif msg_type == "server_evaluation":
                    progress = msg.get("progress", "?")
                    print(f"  <- EVALUATION: progress={progress}")

                elif msg_type == "server_done":
                    summary = msg.get("summary", "Task complete")
                    print(f"\n  === DONE ===")
                    print(f"  {summary}")
                    break

                elif msg_type == "server_error":
                    error_msg = msg.get("message", "Unknown error")
                    print(f"\n  <- ERROR: {error_msg[:200]}")
                    if not msg.get("recoverable", False):
                        break

                else:
                    print(f"  <- {msg_type}: {json.dumps(msg)[:150]}")

        except asyncio.TimeoutError:
            print("\n  Timeout waiting for server response (180s)")

    print(f"\nDone! Total interrupts handled: {interrupt_count}")
    print(f"Total browser actions simulated: {browser.action_count}")


if __name__ == "__main__":
    asyncio.run(test())
