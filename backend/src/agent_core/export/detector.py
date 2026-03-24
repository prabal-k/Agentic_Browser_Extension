"""Auto-detect exportable structured data from agent task output."""

import json


def detect_exportable_data(final_state: dict) -> dict | None:
    """Inspect agent final state for structured data that can be exported.

    Checks (in order):
    1. task_memory.important_data for JSON strings (from extract_listings)
    2. Action history for extracted_data that looks like JSON
    3. task_summary for structured content

    Returns dict with 'data' (list[dict]) and 'source' key, or None.
    """
    # 1. Check task_memory important_data for JSON arrays
    memory = final_state.get("task_memory")
    if memory:
        important = getattr(memory, "important_data", {}) if not isinstance(memory, dict) else memory.get("important_data", {})
        for key, value in (important or {}).items():
            parsed = _try_parse_json_list(value)
            if parsed:
                return {"data": parsed, "source": f"memory:{key}"}

    # 2. Check action history for extracted data
    history = final_state.get("action_history", [])
    for entry in reversed(history):
        result = entry.get("result", {})
        extracted = result.get("extracted_data", "")
        if isinstance(extracted, str) and len(extracted) > 50:
            parsed = _try_parse_json_list(extracted)
            if parsed:
                return {"data": parsed, "source": "action_history"}

    # 3. Check task_summary for structured text
    summary = final_state.get("task_summary", "")
    if summary:
        parsed = _try_parse_json_list(summary)
        if parsed:
            return {"data": parsed, "source": "task_summary"}

    return None


def _try_parse_json_list(text: str) -> list[dict] | None:
    """Try to parse text as JSON. Returns list of dicts or None."""
    if not isinstance(text, str):
        return None

    text = text.strip()

    # Direct JSON parse
    try:
        obj = json.loads(text)
        if isinstance(obj, list) and len(obj) > 0 and isinstance(obj[0], dict):
            return obj
        if isinstance(obj, dict):
            # Check for {items: [...]} pattern (from extract_listings)
            items = obj.get("items")
            if isinstance(items, list) and len(items) > 0 and isinstance(items[0], dict):
                return items
    except (json.JSONDecodeError, ValueError):
        pass

    # Try finding JSON embedded in text (between first [ and last ])
    start = text.find("[")
    end = text.rfind("]")
    if start >= 0 and end > start:
        try:
            obj = json.loads(text[start:end + 1])
            if isinstance(obj, list) and len(obj) > 0 and isinstance(obj[0], dict):
                return obj
        except (json.JSONDecodeError, ValueError):
            pass

    # Try finding JSON object with items key
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        try:
            obj = json.loads(text[start:end + 1])
            items = obj.get("items") if isinstance(obj, dict) else None
            if isinstance(items, list) and len(items) > 0:
                return items
        except (json.JSONDecodeError, ValueError):
            pass

    return None
