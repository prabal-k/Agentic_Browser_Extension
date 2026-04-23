"""Force full LLM evaluate path by returning a failed action result.

smart_evaluate skips LLM on clear success/failure signals. To force the full
EVALUATION_PROMPT path (the one fixed in bug #1), we return an ambiguous
failure — page unchanged, status failed — so smart_evaluate escalates to
the full LLM evaluator.
"""

import asyncio
import copy
import json
import time

import websockets


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
    ],
    "forms": [
        {"name": "Search", "action": "/search", "method": "GET", "field_ids": [1, 2]}
    ],
}


async def test():
    uri = "ws://localhost:8002/ws"
    print(f"Connecting to {uri}")
    print("Goal: Click the Search button (will simulate ambiguous failure)")
    print()

    action_count = 0
    got_full_evaluation = False

    async with websockets.connect(uri) as ws:
        msg = json.loads(await ws.recv())
        print(f"Session: {msg.get('session_id', '?')}\n")

        goal_msg = {
            "type": "client_goal",
            "goal": "Click the search button",
            "dom_snapshot": INITIAL_DOM,
        }
        await ws.send(json.dumps(goal_msg))

        try:
            while True:
                raw = await asyncio.wait_for(ws.recv(), timeout=180)
                msg = json.loads(raw)
                t = msg.get("type", "?")

                if t == "server_action_request" and msg.get("execute"):
                    action_count += 1
                    action = msg.get("action", {})
                    atype = action.get("action_type", "?")
                    print(f"  ACTION #{action_count}: {atype} on elem {action.get('element_id')}")

                    # First action: return ambiguous failure to force full evaluate
                    if action_count == 1:
                        response = {
                            "type": "client_action_result",
                            "action_result": {
                                "status": "failed",
                                "message": "Click did not register — page appears unchanged",
                                "page_changed": False,
                                "execution_time_ms": 1200,
                                "error": "timeout waiting for navigation",
                            },
                            "new_dom_snapshot": copy.deepcopy(INITIAL_DOM),
                        }
                        print("    [Browser] Simulated FAILURE — page unchanged")
                    else:
                        response = {
                            "type": "client_action_result",
                            "action_result": {
                                "status": "success",
                                "message": f"Executed {atype}",
                                "page_changed": True,
                                "execution_time_ms": 150,
                            },
                            "new_dom_snapshot": copy.deepcopy(INITIAL_DOM),
                        }
                    await ws.send(json.dumps(response))

                elif t == "server_evaluation":
                    got_full_evaluation = True
                    prog = msg.get("progress", "?")
                    ev = msg.get("evaluation", {}) or {}
                    succeeded = ev.get("action_succeeded", "?")
                    should_replan = ev.get("should_re_plan", "?")
                    print(f"  EVALUATION: succeeded={succeeded} replan={should_replan} progress={prog}")

                elif t == "server_interrupt":
                    # Auto-dismiss confirms
                    fields = msg.get("fields", [])
                    values = {}
                    for f in fields:
                        fid = f.get("field_id", "")
                        values[fid] = True if f.get("field_type") == "confirm" else "skip"
                    await ws.send(json.dumps({"type": "client_user_response", "values": values}))

                elif t == "server_done":
                    summary = msg.get("summary", "")
                    print(f"\n  DONE — {summary[:200]}")
                    break

                elif t == "server_error":
                    print(f"  ERROR: {msg.get('message', '')[:200]}")
                    if not msg.get("recoverable", False):
                        break

                elif action_count > 10:
                    print("  Too many actions, cancelling")
                    await ws.send(json.dumps({"type": "client_cancel"}))
                    break

        except asyncio.TimeoutError:
            print("  Timeout (180s)")

    print(f"\n=== RESULT ===")
    print(f"Actions: {action_count}")
    print(f"Full evaluation message received: {got_full_evaluation}")


if __name__ == "__main__":
    asyncio.run(test())
