"""E2E test client simulating the sellrclub compound-goal flow.

Exercises the sprint-1 fixes:
- F1: credential prompt triggered on an auth goal with a login form
- F2: multi-step plan decomposition, ~8 success_criteria
- F7: done-premature guard blocks done() when the extracted page says "No routes scheduled"

Stages the browser presents:
  1. sellrclub.com landing (with Sign In button, no form fields)
  2. Login page (email + password inputs)
  3. Dashboard (post-auth)
  4. My Routes page — empty state "No routes scheduled"

Expected behaviour:
- The agent receives 'server_interrupt' with a credentials-prompt before any
  LLM-driven attempt to type into the login form.
- After supplying credentials the auto-type fast path fires.
- When the agent eventually calls done() on the "No routes scheduled" page,
  F7 rewrites the action into a re-plan, rather than finalising.

Usage:  python test_ws_sellrclub.py
"""
import asyncio
import copy
import json
import time

import websockets


LANDING_DOM = {
    "url": "https://sellrclub.com/",
    "title": "SellrClub — Home",
    "timestamp": time.time(),
    "viewport_width": 1440,
    "viewport_height": 900,
    "scroll_position": 0.0,
    "has_more_content_below": True,
    "elements": [
        {
            "element_id": 1,
            "element_type": "heading",
            "tag_name": "h1",
            "text": "SellrClub — Street Sales CRM",
            "attributes": {},
            "is_visible": True,
            "is_enabled": True,
        },
        {
            "element_id": 2,
            "element_type": "button",
            "tag_name": "a",
            "text": "Sign In",
            "attributes": {"href": "/login"},
            "is_visible": True,
            "is_enabled": True,
        },
        {
            "element_id": 3,
            "element_type": "button",
            "tag_name": "a",
            "text": "Features",
            "attributes": {"href": "#features"},
            "is_visible": True,
            "is_enabled": True,
        },
    ],
}

LOGIN_DOM = {
    "url": "https://sellrclub.com/login",
    "title": "SellrClub — Sign In",
    "timestamp": time.time(),
    "viewport_width": 1440,
    "viewport_height": 900,
    "scroll_position": 0.0,
    "elements": [
        {
            "element_id": 10,
            "element_type": "text_input",
            "tag_name": "input",
            "text": "",
            "attributes": {"type": "email", "name": "email", "placeholder": "Email address"},
            "is_visible": True,
            "is_enabled": True,
        },
        {
            "element_id": 11,
            "element_type": "text_input",
            "tag_name": "input",
            "text": "",
            "attributes": {"type": "password", "name": "password", "placeholder": "Password"},
            "is_visible": True,
            "is_enabled": True,
        },
        {
            "element_id": 12,
            "element_type": "button",
            "tag_name": "button",
            "text": "Sign In",
            "attributes": {"type": "submit"},
            "is_visible": True,
            "is_enabled": True,
        },
    ],
    "forms": [{"name": "Login", "action": "/login", "method": "POST", "field_ids": [10, 11, 12]}],
}

DASHBOARD_DOM = {
    "url": "https://sellrclub.com/dashboard",
    "title": "SellrClub — Dashboard",
    "timestamp": time.time(),
    "elements": [
        {
            "element_id": 20,
            "element_type": "heading",
            "tag_name": "h1",
            "text": "Welcome back",
            "attributes": {},
            "is_visible": True,
            "is_enabled": True,
        },
        {
            "element_id": 21,
            "element_type": "link",
            "tag_name": "a",
            "text": "My Routes / Auto Routing",
            "attributes": {"href": "/my-routes"},
            "is_visible": True,
            "is_enabled": True,
        },
        {
            "element_id": 22,
            "element_type": "button",
            "tag_name": "button",
            "text": "Clock In",
            "attributes": {},
            "is_visible": True,
            "is_enabled": True,
        },
    ],
}

ROUTES_EMPTY_DOM = {
    "url": "https://sellrclub.com/my-routes",
    "title": "SellrClub — My Routes",
    "timestamp": time.time(),
    "page_text_summary": "No routes scheduled and no visits assigned for today.",
    "elements": [
        {
            "element_id": 30,
            "element_type": "heading",
            "tag_name": "h1",
            "text": "My Routes",
            "attributes": {},
            "is_visible": True,
            "is_enabled": True,
        },
        {
            "element_id": 31,
            "element_type": "text",
            "tag_name": "p",
            "text": "No routes scheduled and no visits assigned for today.",
            "attributes": {},
            "is_visible": True,
            "is_enabled": True,
        },
        {
            "element_id": 32,
            "element_type": "button",
            "tag_name": "button",
            "text": "Plan Route",
            "attributes": {},
            "is_visible": True,
            "is_enabled": True,
        },
    ],
}


class Simulator:
    """Serve DOM snapshots based on the agent's action stream."""

    def __init__(self):
        self.count = 0
        self.typed_password = False
        self.submitted_login = False
        self.navigated_routes = False
        self.current = copy.deepcopy(LANDING_DOM)

    def run(self, action_request: dict) -> tuple[dict, dict]:
        self.count += 1
        action = action_request.get("action", {})
        at = action.get("action_type", "")
        value = action.get("value", "") or ""
        eid = action.get("element_id")
        print(f"    [Browser] #{self.count} {at} el={eid} val={(value[:40] + '...') if len(value) > 40 else value}")

        if at == "navigate":
            v = value.lower()
            if "login" in v:
                self.current = copy.deepcopy(LOGIN_DOM)
            elif "my-routes" in v or "routes" in v:
                self.current = copy.deepcopy(ROUTES_EMPTY_DOM)
                self.navigated_routes = True
            elif "dashboard" in v:
                self.current = copy.deepcopy(DASHBOARD_DOM)
            elif "sellrclub" in v:
                self.current = copy.deepcopy(LANDING_DOM)
            return {"status": "success", "message": f"Navigated to {value[:60]}"}, self.current

        if at in ("click",):
            # Sign In button on landing → login page
            if self.current.get("url", "").endswith(".com/") and eid == 2:
                self.current = copy.deepcopy(LOGIN_DOM)
                return {"status": "success", "message": "Landed on login page"}, self.current
            # Submit button on login page → dashboard (only after password typed)
            if "login" in self.current.get("url", "") and eid == 12 and self.typed_password:
                self.submitted_login = True
                self.current = copy.deepcopy(DASHBOARD_DOM)
                return {"status": "success", "message": "Signed in, dashboard loaded"}, self.current
            # My Routes link on dashboard
            if "dashboard" in self.current.get("url", "") and eid == 21:
                self.current = copy.deepcopy(ROUTES_EMPTY_DOM)
                self.navigated_routes = True
                return {"status": "success", "message": "My Routes page"}, self.current
            # Plan Route button on empty routes page — still empty
            if eid == 32:
                self.current["timestamp"] = time.time()
                return {"status": "success", "message": "Plan Route clicked, no routes generated"}, self.current
            return {"status": "success", "message": f"Click {eid}"}, self.current

        if at in ("type_text", "clear_and_type"):
            # Mark password typed when the password field (id 11) is hit
            if eid == 11:
                self.typed_password = True
            # Visually update the field value
            for el in self.current.get("elements", []):
                if el.get("element_id") == eid:
                    el["text"] = value.split("|")[0]
                    el.setdefault("attributes", {})["value"] = value.split("|")[0]
            self.current["timestamp"] = time.time()
            return {"status": "success", "message": f"Typed into {eid}"}, self.current

        if at in ("extract_text", "read_page"):
            snippet = self.current.get("page_text_summary") or ""
            if not snippet:
                # Compose from visible elements
                texts = [e.get("text", "") for e in self.current.get("elements", []) if e.get("text")]
                snippet = " / ".join(texts)[:400]
            return {
                "status": "success",
                "message": "Page text extracted",
                "extracted_data": snippet,
            }, self.current

        # Default: no-op success
        self.current["timestamp"] = time.time()
        return {"status": "success", "message": f"{at} ok"}, self.current


async def test():
    uri = "ws://localhost:8002/ws"
    print(f"Connecting to {uri}")
    goal = (
        "open sellrclub.com , signin with the credenitails then go to my-routes page . "
        "now plan the route , clockin into the system , provide the initial strating address "
        "of naples florida usa , and auto assign the default businesses . "
        "And check if the 20 businesses has been assigned for today and a optimized routes has been created or not ."
    )
    sim = Simulator()

    async with websockets.connect(uri) as ws:
        msg = json.loads(await ws.recv())
        print(f"Connected session={msg.get('session_id', '?')}\n")
        print(f"GOAL: {goal}\n")

        await ws.send(json.dumps({
            "type": "client_goal",
            "goal": goal,
            "dom_snapshot": LANDING_DOM,
        }))

        interrupt_count = 0
        saw_credential_prompt = False
        saw_done = False
        final_summary = ""

        while True:
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=240)
            except asyncio.TimeoutError:
                print("  !! Timeout 240s")
                break
            m = json.loads(raw)
            t = m.get("type", "?")

            if t == "server_status":
                print(f"  STATUS: {m.get('status', '')}")
            elif t == "server_reasoning":
                print(f"  REASON: {m.get('reasoning', '')[:160]}")
            elif t == "server_plan":
                steps = m.get("steps", [])
                print(f"  PLAN ({len(steps)} steps):")
                for s in steps[:12]:
                    if isinstance(s, dict):
                        print(f"    - {s.get('description', s)[:80]}")
                    else:
                        print(f"    - {str(s)[:80]}")
            elif t == "server_action_request":
                action = m.get("action", {})
                at = action.get("action_type", "?")
                eid = action.get("element_id", "?")
                print(f"  ACTION REQ: {at} el={eid}")
                if m.get("execute"):
                    result, new_dom = sim.run(m)
                    await ws.send(json.dumps({
                        "type": "client_action_result",
                        "action_result": {
                            **result,
                            "page_changed": True,
                            "execution_time_ms": 120,
                        },
                        "new_dom_snapshot": new_dom,
                    }))
            elif t == "server_interrupt":
                interrupt_count += 1
                title = m.get("title", "")
                ctx = m.get("context", "")
                fields = m.get("fields", [])
                labels = " | ".join(f.get("label", "") for f in fields)
                print(f"  INTERRUPT #{interrupt_count}: {title} :: {labels[:160]}")
                is_cred = (
                    "credential" in ctx.lower()
                    or "credential" in labels.lower()
                    or "sign in" in labels.lower()
                    or "sign in" in ctx.lower()
                )
                values = {}
                for f in fields:
                    fid = f.get("field_id", "")
                    ftype = f.get("field_type", "")
                    if ftype == "confirm" or fid == "confirmed":
                        values["confirmed"] = True
                    elif ftype == "text" or fid == "answer":
                        if is_cred:
                            saw_credential_prompt = True
                            values["answer"] = (
                                "my email is tester@example.com and password is SecretP@ss2026"
                            )
                        else:
                            values["answer"] = "Yes, go ahead."
                    else:
                        values[fid] = "yes"
                await ws.send(json.dumps({"type": "client_user_response", "values": values}))
                if interrupt_count > 20:
                    print("  !! too many interrupts, stopping")
                    await ws.send(json.dumps({"type": "client_cancel"}))
                    break
            elif t == "server_evaluation":
                print(f"  EVAL: {m.get('progress', '?')}")
            elif t == "server_done":
                saw_done = True
                final_summary = m.get("summary", "")
                print(f"\n  === DONE ===\n  {final_summary[:600]}")
                break
            elif t == "server_error":
                print(f"  ERROR: {m.get('message', '')[:200]}")
                if not m.get("recoverable", False):
                    break
            else:
                print(f"  {t}: {json.dumps(m)[:160]}")

    print("\n--- summary ---")
    print(f"interrupts: {interrupt_count}")
    print(f"saw credential prompt (F1): {saw_credential_prompt}")
    print(f"reached my-routes page: {sim.navigated_routes}")
    print(f"password typed (auto-type path): {sim.typed_password}")
    print(f"login submitted: {sim.submitted_login}")
    print(f"saw done: {saw_done}")


if __name__ == "__main__":
    asyncio.run(test())
