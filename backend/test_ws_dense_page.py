"""Exercise page-context compression with a dense page (120+ elements)."""

import asyncio
import copy
import json
import time

import websockets


def build_dense_dom():
    elements = [
        {
            "element_id": 1,
            "element_type": "text_input",
            "tag_name": "input",
            "text": "",
            "attributes": {"type": "text", "name": "q", "placeholder": "Search products..."},
            "is_visible": True,
            "is_enabled": True,
            "is_focused": False,
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
            "parent_context": "inside form: Search",
            "css_selector": "button[type='submit']",
        },
    ]
    # 120 product tiles (link + button pairs) like a typical SERP
    for i in range(3, 123):
        elements.append({
            "element_id": i,
            "element_type": "link" if i % 2 == 0 else "button",
            "tag_name": "a" if i % 2 == 0 else "button",
            "text": f"Product Tile Title Number {i} With Long Descriptive Name",
            "attributes": {"href": f"/p/item-{i}"} if i % 2 == 0 else {"type": "button"},
            "is_visible": True,
            "is_enabled": True,
            "parent_context": f"product card {i}",
        })
    return {
        "url": "https://example-shop.com/search?q=phone",
        "title": "Search Results",
        "timestamp": time.time(),
        "viewport_width": 1920,
        "viewport_height": 1080,
        "scroll_position": 0.0,
        "has_more_content_below": True,
        "page_text_summary": "Showing 120 results for 'phone'. Prices range from $99 to $1299.",
        "elements": elements,
        "forms": [
            {"name": "Search", "action": "/search", "method": "GET", "field_ids": [1, 2]}
        ],
    }


async def test():
    uri = "ws://localhost:8002/ws"
    dense = build_dense_dom()
    print(f"Goal on dense page ({len(dense['elements'])} elements)")
    print()

    async with websockets.connect(uri) as ws:
        msg = json.loads(await ws.recv())
        print(f"Session: {msg.get('session_id', '?')}")

        goal_msg = {
            "type": "client_goal",
            "goal": "Type 'iphone 15' in the search box",
            "dom_snapshot": dense,
        }
        await ws.send(json.dumps(goal_msg))

        action_count = 0
        try:
            while True:
                raw = await asyncio.wait_for(ws.recv(), timeout=180)
                msg = json.loads(raw)
                t = msg.get("type", "?")

                if t == "server_stream_end":
                    toks = msg.get("total_tokens", "?")
                    print(f"  LLM output tokens: {toks}")

                elif t == "server_action_request" and msg.get("execute"):
                    action_count += 1
                    action = msg.get("action", {})
                    atype = action.get("action_type", "?")
                    print(f"  ACTION #{action_count}: {atype}")
                    new_dom = copy.deepcopy(dense)
                    new_dom["timestamp"] = time.time()
                    if atype in ("type_text", "clear_and_type"):
                        new_dom["elements"][0]["text"] = action.get("value", "")
                        new_dom["elements"][0]["attributes"]["value"] = action.get("value", "")
                        new_dom["elements"][0]["is_focused"] = True
                    response = {
                        "type": "client_action_result",
                        "action_result": {
                            "status": "success",
                            "message": f"Executed {atype}",
                            "page_changed": True,
                            "execution_time_ms": 150,
                        },
                        "new_dom_snapshot": new_dom,
                    }
                    await ws.send(json.dumps(response))

                elif t == "server_done":
                    print(f"\n  DONE — {msg.get('summary', '')[:120]}")
                    break

                elif t == "server_error":
                    print(f"  ERROR: {msg.get('message', '')[:200]}")
                    if not msg.get("recoverable", False):
                        break

                if action_count > 6:
                    await ws.send(json.dumps({"type": "client_cancel"}))
                    break

        except asyncio.TimeoutError:
            print("  Timeout")


if __name__ == "__main__":
    asyncio.run(test())
