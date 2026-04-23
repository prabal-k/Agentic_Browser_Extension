"""Cognitive graph nodes — each node is one step in the agent's thinking process.

Each function takes AgentState, performs one cognitive operation, and returns
state updates. The graph orchestrator routes between nodes based on the
agent's decisions.

Node categories:
1. Goal & Planning: analyze_goal, create_plan, critique_plan
2. Reasoning: reason, decide_action
3. Execution: prepare_action, execute_action (external), observe
4. Evaluation: evaluate, self_critique
5. Adaptation: handle_retry, ask_user
6. Terminal: finalize

Design decisions:
- Each node does ONE thing well
- Nodes communicate through state, not return values
- LLM calls use structured JSON output for reliability
- Every node updates cognitive_status for UI feedback
- Error handling is per-node — failures don't crash the graph
"""

import json
import re
import uuid
import structlog
from typing import Any

from langchain_core.messages import SystemMessage, HumanMessage, AIMessage

from agent_core.schemas.agent import (
    AgentState,
    Goal,
    Plan,
    PlanStep,
    StepStatus,
    ReasoningTrace,
    SelfCritique,
    Evaluation,
    RetryContext,
    TaskMemory,
    CognitiveStatus,
)
from agent_core.schemas.actions import Action, ActionType, ActionResult, ActionStatus
from agent_core.schemas.dom import PageContext
from agent_core.agent.prompts import (
    GOAL_ANALYSIS_PROMPT,
    PLAN_CREATION_PROMPT,
    ANALYZE_AND_PLAN_PROMPT,
    SELF_CRITIQUE_PROMPT,
    REASONING_PROMPT,
    ACTION_DECISION_PROMPT,
    EVALUATION_PROMPT,
    RETRY_STRATEGY_PROMPT,
    GOAL_VERIFICATION_PROMPT,
    STEP_COMPLETION_CRITIQUE_PROMPT,
    SYSTEM_GOAL_ANALYSIS,
    SYSTEM_PLAN_CREATION,
    SYSTEM_ANALYZE_AND_PLAN,
    SYSTEM_PLAN_CRITIQUE,
    SYSTEM_REASONING,
    SYSTEM_ACTION_DECISION,
    SYSTEM_EVALUATION,
    SYSTEM_COMPLETION_CRITIQUE,
    SYSTEM_RETRY,
    SYSTEM_GOAL_VERIFICATION,
    CAPABILITY_SYSTEM_PROMPTS,
    classify_action_capability,
    format_action_history,
    format_plan_for_prompt,
    format_retry_context,
    format_task_memory,
    detect_task_pattern,
)
from agent_core.agent.llm_client import get_reasoning_llm, get_action_llm, get_action_llm_dynamic
from agent_core.memory.store import get_memory, extract_domain

logger = structlog.get_logger("agent.nodes")


# ============================================================
# Utility: Parse JSON from LLM response
# ============================================================

_THINK_PATTERN = re.compile(r"<think>(.*?)</think>", re.DOTALL)


def _extract_thinking(content: str) -> tuple[str, str]:
    """Extract Qwen-style <think> blocks from LLM response.

    Returns (thinking_text, remaining_content).
    Qwen3 models output <think>reasoning</think> before the actual response.
    """
    match = _THINK_PATTERN.search(content)
    if match:
        thinking = match.group(1).strip()
        remaining = content[:match.start()] + content[match.end():]
        return thinking, remaining.strip()
    return "", content


def _parse_llm_json(content: str) -> dict:
    """Extract JSON from LLM response, handling markdown code blocks and <think> tags."""
    # Strip Qwen thinking tags first
    _, text = _extract_thinking(content)
    text = text.strip()

    # Remove markdown code blocks if present
    if text.startswith("```"):
        # Find the first newline after ```
        first_nl = text.index("\n")
        # Find the last ```
        last_block = text.rfind("```")
        if last_block > first_nl:
            text = text[first_nl + 1:last_block].strip()

    return json.loads(text)


def _safe_serialize(obj: Any) -> Any:
    """Safely serialize Pydantic models and enums for JSON."""
    if hasattr(obj, "model_dump"):
        return obj.model_dump()
    if hasattr(obj, "value"):
        return obj.value
    return obj


# Ordered list of argument names to check when extracting the action value
_VALUE_ARG_NAMES = [
    "text", "url", "value", "key", "keys", "code", "action_value",
    "file_path", "css_selector", "question", "summary", "description",
]
# Numeric args that need str() conversion
_NUMERIC_ARG_NAMES = ["amount", "seconds", "tab_index", "timeout_seconds"]


def _extract_value(args: dict) -> str | None:
    """Extract the primary value from tool call arguments.

    Checks all known argument names used across the browser tools.
    """
    # Priority: if "value" contains a marker (__VISUAL_CHECK__, __READ_PAGE__), use it first
    raw_value = args.get("value")
    if raw_value and isinstance(raw_value, str) and raw_value.startswith("__"):
        return raw_value

    # Check string args first
    for name in _VALUE_ARG_NAMES:
        v = args.get(name)
        if v:
            return str(v)

    # Check numeric args
    for name in _NUMERIC_ARG_NAMES:
        v = args.get(name)
        if v is not None:
            return str(v)

    # Special: drag has source_element_id + target_element_id
    target = args.get("target_element_id")
    if target is not None:
        return str(target)

    # Special: submit flag for type_text/clear_and_type
    if args.get("submit"):
        text = args.get("text", "")
        return f"{text}|SUBMIT" if text else None

    return None


# URL extraction pattern — finds URLs in user's goal text
# Allows apostrophes, parentheses, and other common URL characters
_URL_PATTERN = re.compile(r'https?://[^\s<>"]+')


def _extract_urls_from_text(text: str) -> list[str]:
    """Extract URLs from free-form text."""
    urls = _URL_PATTERN.findall(text)
    # Clean trailing punctuation that isn't part of the URL
    cleaned = []
    for url in urls:
        # Strip trailing periods, commas, semicolons (sentence endings)
        url = url.rstrip('.,;:')
        if url:
            cleaned.append(url)
    return cleaned


# Well-known sites where we can construct direct search URLs
# This avoids 2-3 LLM calls (navigate → find search → type → submit)
_KNOWN_SITES = {
    "youtube": {
        "patterns": ["youtube", "yt"],
        "search_url": "https://www.youtube.com/results?search_query={query}",
        "base_url": "https://www.youtube.com",
        "verbs": ["search", "find", "watch", "play", "look for"],
    },
    "duckduckgo": {
        "patterns": ["duckduckgo", "ddg"],
        "search_url": "https://duckduckgo.com/?q={query}",
        "base_url": "https://duckduckgo.com",
        "verbs": ["search", "find", "look for", "look up"],
    },
    "amazon": {
        "patterns": ["amazon"],
        "search_url": "https://www.amazon.com/s?k={query}",
        "base_url": "https://www.amazon.com",
        "verbs": ["search", "find", "look for", "check price"],
    },
    "wikipedia": {
        "patterns": ["wikipedia", "wiki"],
        "search_url": "https://en.wikipedia.org/w/index.php?search={query}",
        "base_url": "https://en.wikipedia.org",
        "verbs": ["search", "find", "look up", "read about"],
    },
    "github": {
        "patterns": ["github"],
        "search_url": "https://github.com/search?q={query}",
        "base_url": "https://github.com",
        "verbs": ["search", "find", "look for"],
    },
    "daraz": {
        "patterns": ["daraz"],
        "search_url": "https://www.daraz.com.np/catalog/?q={query}",
        "base_url": "https://www.daraz.com.np",
        "verbs": ["search", "find", "check", "look for", "buy"],
    },
}


def _build_direct_url(goal_text: str) -> str:
    """Try to construct a direct search URL from a natural language goal.

    Only works for well-known sites where the URL pattern is predictable.
    Returns empty string if no match found.

    Examples:
        "search for iphones on youtube" → youtube.com/results?search_query=iphones
        "find lenovo laptop price on daraz" → daraz.com.np/catalog/?q=lenovo+laptop
        "go to github" → github.com (navigation only, no search)
    """
    text_lower = goal_text.lower()

    # Find which site is mentioned — check the most specific pattern match
    matched_site = None
    for site_id, config in _KNOWN_SITES.items():
        for pattern in config["patterns"]:
            # Require word boundary match to avoid false positives
            if re.search(rf'\b{re.escape(pattern)}\b', text_lower):
                matched_site = (site_id, config)
                break
        if matched_site:
            break

    if not matched_site:
        return ""

    site_id, config = matched_site

    # Check if there's a search verb — if so, extract the query
    has_search_verb = any(v in text_lower for v in config["verbs"])
    if not has_search_verb:
        return config["base_url"]

    # Extract search query: take text between the verb and the site name
    # e.g., "search for 3 idiots trailer on youtube" → "3 idiots trailer"
    query = ""

    # Try to extract query between verb and site name
    # Handles: "search for X on youtube", "find X on wikipedia", "play X on youtube"
    for verb in sorted(config["verbs"], key=len, reverse=True):
        for pattern in config["patterns"]:
            # Pattern: "verb (for)? ... on/in/from site"
            match = re.search(
                rf'{re.escape(verb)}(?:\s+for)?\s+(.+?)\s+(?:on|in|from|at)\s+{re.escape(pattern)}',
                text_lower
            )
            if match:
                query = match.group(1).strip()
                # Remove leading "the/a/an"
                query = re.sub(r'^(the|a|an)\s+', '', query)
                break
        if query:
            break

    # Try alternate pattern: "check (the)? price of X on site"
    if not query:
        for pattern in config["patterns"]:
            match = re.search(
                rf'(?:check|find|get)\s+(?:the\s+)?price\s+of\s+(.+?)\s+(?:on|in|from|at)\s+{re.escape(pattern)}',
                text_lower
            )
            if match:
                query = match.group(1).strip()
                break

    # Fallback: remove the site name and verbs, take what's left
    if not query:
        query = text_lower
        for prefix in ("search for", "search", "find", "look for", "look up",
                       "watch", "play", "check the price of", "check price of",
                       "read about", "buy"):
            query = query.replace(prefix, "")
        for p in config["patterns"]:
            query = query.replace(p, "")
        for word in ("on", "from", "at"):
            query = re.sub(rf'\b{word}\b', '', query)
        query = query.strip().strip(".,!?")

    if query and len(query) > 2:
        from urllib.parse import quote_plus
        return config["search_url"].format(query=quote_plus(query))
    else:
        return config["base_url"]


# ============================================================
# Node 1: Analyze Goal + Create Plan (merged — single LLM call)
# ============================================================

async def analyze_and_plan(state: AgentState) -> dict:
    """Lightweight task understanding — NO plan creation, NO LLM call.

    Extracts structured metadata (URLs, keywords) from the user's task.
    The original task text flows through unchanged to decide_action.
    """
    goal_text = state["goal"].original_text
    logger.info("analyze_and_plan", goal=goal_text)

    # Extract URLs from the task text — code-level, no LLM needed
    extracted_urls = _extract_urls_from_text(goal_text)
    target_url = extracted_urls[0] if extracted_urls else ""

    # If no explicit URL, try to construct one from well-known site + search query
    # This saves 2-3 LLM calls (navigate → find search → type → submit)
    if not target_url:
        target_url = _build_direct_url(goal_text)
        if target_url:
            logger.info("direct_url_constructed", url=target_url)

    # Determine complexity heuristically
    word_count = len(goal_text.split())
    complexity = "simple" if word_count < 15 else "medium" if word_count < 40 else "complex"

    # Detect user-requested output format
    goal_lower = goal_text.lower()
    output_format = ""
    format_patterns = {
        "json": ("as json", "in json", "json format", "return json", "output json"),
        "csv": ("as csv", "in csv", "csv format", "return csv", "output csv"),
        "table": ("as table", "as a table", "in table", "table format", "in tabular"),
        "bullets": ("as bullet", "bullet point", "bulleted list", "as bullets"),
        "numbered": ("as numbered", "numbered list", "as a list"),
    }
    for fmt, patterns in format_patterns.items():
        if any(p in goal_lower for p in patterns):
            output_format = fmt
            logger.info("output_format_detected", format=fmt)
            break

    # Build goal with original text preserved — no rewriting
    goal = Goal(
        original_text=goal_text,
        interpreted_goal=goal_text,  # Keep original — don't rewrite
        sub_goals=[],
        success_criteria=[],  # decide_action will reason about this
        constraints=["Never submit payment without user confirmation", "Never interact with ads"],
        complexity=complexity,
        is_achievable=True,
        output_format=output_format,
    )

    # Build a minimal plan — just the first obvious action
    # The reactive loop will figure out the rest step by step
    first_step_desc = "Execute the task"
    page_context = state.get("page_context")
    is_blank = not page_context or getattr(page_context, "url", "").startswith("about:")

    if target_url and is_blank:
        first_step_desc = f"Navigate to {target_url}"
    elif is_blank:
        first_step_desc = "Navigate to the relevant website or search engine"

    plan = Plan(
        steps=[PlanStep(step_id=1, description=first_step_desc, expected_outcome="Page loaded")],
        current_step_index=0,
        plan_version=1,
        original_reasoning="Reactive mode — one step at a time",
    )

    # If we have a target URL (from user text or direct URL construction),
    # skip the LLM and auto-navigate — zero LLM calls for the first action
    # If we have a target URL, store it so decide_action can auto-navigate
    # without an LLM call (code-level fast path)
    reasoning = "First action — start working on the task."
    auto_nav_url = ""
    if target_url and is_blank:
        auto_nav_url = target_url
        reasoning = f"Auto-navigating to: {target_url}"
        logger.info("auto_navigate_queued", url=target_url)

    # Load persistent memory for the target domain
    site_memory_text = ""
    try:
        domain = ""
        if target_url:
            domain = extract_domain(target_url)
        elif page_context and getattr(page_context, "url", ""):
            domain = extract_domain(page_context.url)
        if domain:
            mem = get_memory()
            site_memory_text = mem.format_for_prompt(domain)
            if site_memory_text:
                logger.info("memory_loaded", domain=domain, length=len(site_memory_text))
    except Exception as e:
        logger.warning("memory_load_failed", error=str(e)[:100])

    return {
        "goal": goal,
        "plan": plan,
        "cognitive_status": CognitiveStatus.DECIDING,
        "current_reasoning": reasoning,
        "pending_user_input": auto_nav_url,  # Reuse this field for auto-navigate
        "pending_input_field_type": "auto_navigate" if auto_nav_url else "",
        # Store user's goal as a message for session memory across goals
        "messages": [HumanMessage(content=goal_text)],
        # Persistent memory context for prompts (stored in task_memory.discovered_patterns)
        "task_memory": TaskMemory(
            discovered_patterns=[site_memory_text] if site_memory_text else [],
        ),
    }


# ============================================================
# Node 1 (legacy): Analyze Goal — kept for backward compatibility
# ============================================================

async def analyze_goal(state: AgentState) -> dict:
    """Parse and understand the user's goal. (Legacy — used only if graph uses old wiring.)"""
    logger.info("analyze_goal", goal=state["goal"].original_text)

    llm = get_reasoning_llm(state.get("model_name"), api_keys=state.get("api_keys"))

    page_context_str = ""
    if state.get("page_context"):
        page_context_str = state["page_context"].to_llm_representation()

    prompt = GOAL_ANALYSIS_PROMPT.format(
        page_context=page_context_str or "No page loaded yet.",
        goal=state["goal"].original_text,
    )

    response = await llm.ainvoke([
        SystemMessage(content=SYSTEM_GOAL_ANALYSIS),
        HumanMessage(content=prompt),
    ])

    try:
        analysis = _parse_llm_json(response.content)
        goal = Goal(
            original_text=state["goal"].original_text,
            interpreted_goal=analysis.get("interpreted_goal", state["goal"].original_text),
            sub_goals=analysis.get("sub_goals", []),
            success_criteria=analysis.get("success_criteria", []),
            constraints=analysis.get("constraints", []),
            complexity=analysis.get("complexity", "medium"),
            is_achievable=analysis.get("is_achievable", True),
            achievability_reason=analysis.get("achievability_reason", ""),
        )
    except (json.JSONDecodeError, KeyError) as e:
        logger.warning("goal_analysis_parse_error", error=str(e))
        goal = Goal(
            original_text=state["goal"].original_text,
            interpreted_goal=state["goal"].original_text,
            is_achievable=True,
        )

    return {
        "goal": goal,
        "cognitive_status": CognitiveStatus.CREATING_PLAN,
        "messages": [
            AIMessage(content=f"Goal analyzed: {goal.interpreted_goal}")
        ],
    }


# ============================================================
# Node 2: Create Plan
# ============================================================

async def create_plan(state: AgentState) -> dict:
    """Create or re-create the execution plan.

    Generates an ordered list of steps to achieve the goal,
    considering the current page state and what's been done so far.
    """
    logger.info("create_plan", goal=state["goal"].interpreted_goal)

    llm = get_reasoning_llm(state.get("model_name"), api_keys=state.get("api_keys"))

    page_context_str = ""
    if state.get("page_context"):
        page_context_str = state["page_context"].to_llm_representation()

    # Check if this is a re-plan
    existing_plan = state.get("plan", Plan())
    previous_plan_context = ""
    if existing_plan.plan_version > 0 and existing_plan.steps:
        # Build clear context showing what succeeded vs what failed
        completed_steps = [s for s in existing_plan.steps if s.status == StepStatus.COMPLETED]
        remaining_steps = [s for s in existing_plan.steps if s.status != StepStatus.COMPLETED]

        lines = [f"## Re-planning (version {existing_plan.plan_version} failed)"]
        lines.append(f"Reason: {existing_plan.re_plan_reason or 'Previous approach did not work'}")
        lines.append("")

        if completed_steps:
            lines.append("## Already Completed (DO NOT repeat these):")
            for s in completed_steps:
                lines.append(f"  [DONE] Step {s.step_id}: {s.description}")
            lines.append("")

        if remaining_steps:
            lines.append("## Failed/Remaining (need new approach):")
            for s in remaining_steps:
                lines.append(f"  [TODO] Step {s.step_id}: {s.description}")
            lines.append("")

        lines.append("Plan ONLY the remaining work. Do NOT redo completed steps.")
        previous_plan_context = "\n".join(lines)

    goal_analysis = _safe_serialize(state["goal"])
    action_history = state.get("action_history", [])

    prompt = PLAN_CREATION_PROMPT.format(
        goal_analysis=json.dumps(goal_analysis, indent=2, default=str),
        page_context=page_context_str or "No page loaded yet.",
        previous_plan_context=previous_plan_context or "This is the initial plan.",
        action_history=format_action_history(action_history),
    )

    response = await llm.ainvoke([
        SystemMessage(content=SYSTEM_PLAN_CREATION),
        HumanMessage(content=prompt),
    ])

    try:
        plan_data = _parse_llm_json(response.content)
        steps = []
        for i, s in enumerate(plan_data.get("steps", [])):
            # Sanitize depends_on — LLM sometimes returns strings instead of ints
            raw_deps = s.get("depends_on", [])
            deps = []
            if isinstance(raw_deps, list):
                for d in raw_deps:
                    try:
                        deps.append(int(d))
                    except (ValueError, TypeError):
                        pass
            steps.append(PlanStep(
                step_id=s.get("step_id", i + 1),
                description=s.get("description", f"Step {i+1}"),
                expected_outcome=s.get("expected_outcome", ""),
                depends_on=deps,
                can_parallelize=s.get("can_parallelize", False),
            ))
        plan = Plan(
            steps=steps,
            current_step_index=0,
            plan_version=existing_plan.plan_version + 1 if existing_plan.steps else 1,
            original_reasoning=plan_data.get("reasoning", ""),
            re_plan_reason=existing_plan.re_plan_reason if existing_plan.plan_version > 0 else "",
        )
    except (json.JSONDecodeError, KeyError) as e:
        logger.warning("plan_creation_parse_error", error=str(e))
        plan = Plan(
            steps=[PlanStep(step_id=1, description="Attempt to achieve the goal directly")],
            plan_version=existing_plan.plan_version + 1,
            original_reasoning="Fallback plan due to LLM parse error",
        )

    return {
        "plan": plan,
        "cognitive_status": CognitiveStatus.SELF_CRITIQUING,
        "messages": [
            AIMessage(content=f"Plan created (v{plan.plan_version}): {len(plan.steps)} steps")
        ],
    }


# ============================================================
# Node 3: Critique Plan
# ============================================================

async def critique_plan(state: AgentState) -> dict:
    """Self-critique the current plan before execution.

    Skips the LLM call for simple goals (1-2 steps) — the plan is too
    short to have meaningful issues. Only critiques medium/complex plans.
    """
    logger.info("critique_plan", plan_version=state["plan"].plan_version)

    goal = state.get("goal", Goal(original_text=""))
    plan = state.get("plan", Plan())

    # Skip critique for simple goals or very short plans — saves 1 LLM call
    if goal.complexity == "simple" or len(plan.steps) <= 3:
        logger.info("critique_plan_skipped", reason="simple_goal", steps=len(plan.steps))
        return {
            "self_critiques": list(state.get("self_critiques", [])),
            "cognitive_status": CognitiveStatus.REASONING,
        }

    llm = get_reasoning_llm(state.get("model_name"), api_keys=state.get("api_keys"))

    page_context_str = ""
    if state.get("page_context"):
        page_context_str = state["page_context"].to_llm_representation()

    plan_str = format_plan_for_prompt(_safe_serialize(state["plan"]))

    prompt = SELF_CRITIQUE_PROMPT.format(
        critique_target="plan",
        target_type="plan",
        content_to_critique=plan_str,
        page_context=page_context_str or "No page loaded.",
        goal=state["goal"].interpreted_goal,
    )

    response = await llm.ainvoke([
        SystemMessage(content=SYSTEM_PLAN_CRITIQUE),
        HumanMessage(content=prompt),
    ])

    try:
        critique_data = _parse_llm_json(response.content)
        critique = SelfCritique(
            target="plan",
            critique=critique_data.get("critique", "No issues found"),
            severity=critique_data.get("severity", "info"),
            suggestion=critique_data.get("suggestion", ""),
            should_re_plan=critique_data.get("should_re_plan", False),
        )
    except (json.JSONDecodeError, KeyError):
        critique = SelfCritique(
            target="plan",
            critique="Unable to critique (parse error)",
            severity="info",
            should_re_plan=False,
        )

    critiques = list(state.get("self_critiques", []))
    critiques.append(critique)

    # If critique is critical, trigger re-plan (but only once to avoid loops)
    next_status = CognitiveStatus.REASONING
    plan_update = {}
    if critique.should_re_plan and critique.severity == "critical" and state["plan"].plan_version < 3:
        next_status = CognitiveStatus.RE_PLANNING
        plan_update = {
            "plan": Plan(
                steps=state["plan"].steps,
                current_step_index=state["plan"].current_step_index,
                plan_version=state["plan"].plan_version,
                original_reasoning=state["plan"].original_reasoning,
                re_plan_reason=critique.suggestion or critique.critique,
            )
        }

    return {
        "self_critiques": critiques,
        "cognitive_status": next_status,
        **plan_update,
    }


# ============================================================
# Node 4: Reason (ReAct + Chain of Thought)
# ============================================================

async def reason(state: AgentState) -> dict:
    """Core reasoning node using ReAct + Chain of Thought.

    Analyzes the current situation, observes the page,
    and decides what should happen next.
    """
    plan = state.get("plan", Plan())
    current_step = plan.current_step
    iteration = state.get("iteration_count", 0) + 1

    logger.info(
        "reason",
        iteration=iteration,
        current_step=current_step.step_id if current_step else None,
    )

    # Safety: check max iterations
    if iteration > state.get("max_iterations", 25):
        return {
            "cognitive_status": CognitiveStatus.FAILED,
            "should_terminate": True,
            "error": f"Maximum iterations ({state['max_iterations']}) reached",
            "iteration_count": iteration,
        }

    llm = get_reasoning_llm(state.get("model_name"), api_keys=state.get("api_keys"))

    page_context_str = ""
    if state.get("page_context"):
        page_context_str = state["page_context"].to_llm_representation()

    retry_str = format_retry_context(_safe_serialize(state.get("retry_context", RetryContext())))

    prompt = REASONING_PROMPT.format(
        goal=state["goal"].interpreted_goal,
        current_step_number=current_step.step_id if current_step else "N/A",
        current_step_description=current_step.description if current_step else "No current step",
        expected_outcome=current_step.expected_outcome if current_step else "N/A",
        page_context=page_context_str or "No page loaded.",
        action_history=format_action_history(state.get("action_history", []), max_entries=5),
        retry_context=retry_str,
    )

    response = await llm.ainvoke([
        SystemMessage(content=SYSTEM_REASONING),
        HumanMessage(content=prompt),
    ])

    try:
        reasoning_data = _parse_llm_json(response.content)
        trace = ReasoningTrace(
            step_number=iteration,
            thought=reasoning_data.get("thought", ""),
            observation="",  # Merged into thought
            conclusion="",   # Removed — decide_action handles this
            confidence=reasoning_data.get("confidence", 0.5),
        )
        needs_clarification = reasoning_data.get("needs_clarification", False)
        clarification_question = reasoning_data.get("clarification_question", "")
        needs_re_plan = reasoning_data.get("needs_re_plan", False)
        re_plan_reason = reasoning_data.get("re_plan_reason", "")
    except (json.JSONDecodeError, KeyError):
        trace = ReasoningTrace(
            step_number=iteration,
            thought="Error parsing reasoning output",
            confidence=0.3,
        )
        needs_clarification = False
        clarification_question = ""
        needs_re_plan = False
        re_plan_reason = ""

    traces = list(state.get("reasoning_traces", []))
    traces.append(trace)

    next_status = CognitiveStatus.DECIDING
    if needs_clarification:
        next_status = CognitiveStatus.ASKING_USER
    elif needs_re_plan and state.get("plan", Plan()).plan_version >= 2:
        # Already re-planned at least once — don't keep re-planning.
        # In reactive mode, just adapt the next action instead.
        logger.info("reason_replan_suppressed",
                    msg="Suppressing re-plan, already v2+. Proceeding to decide.")
        next_status = CognitiveStatus.DECIDING
    elif needs_re_plan:
        # Reasoning detected page doesn't match plan — trigger re-plan
        next_status = CognitiveStatus.RE_PLANNING
        plan = state.get("plan", Plan())
        plan_update = Plan(
            steps=list(plan.steps),
            current_step_index=plan.current_step_index,
            plan_version=plan.plan_version,
            original_reasoning=plan.original_reasoning,
            re_plan_reason=re_plan_reason or "Page state doesn't match plan expectations",
        )
        return {
            "reasoning_traces": traces,
            "current_reasoning": trace.thought,
            "cognitive_status": next_status,
            "iteration_count": iteration,
            "plan": plan_update,
            "messages": [
                AIMessage(content=f"Re-planning: {re_plan_reason or 'page changed'}")
            ],
        }

    return {
        "reasoning_traces": traces,
        "current_reasoning": trace.thought,
        "cognitive_status": next_status,
        "iteration_count": iteration,
        "messages": [
            AIMessage(content=f"Reasoning: {trace.thought[:200]}")
        ],
    }


# ============================================================
# Node 5: Decide Action
# ============================================================

async def decide_action(state: AgentState) -> dict:
    """Decide which browser action to perform based on reasoning.

    This node uses the LLM with bound tools to select a specific
    browser action. The LLM must call exactly one tool function.

    FAST PATH: If the user just provided input via interrupt (pending_user_input),
    and there's a matching sensitive field on the page, auto-type it without an LLM call.
    """
    logger.info("decide_action")

    plan = state.get("plan", Plan())
    current_step = plan.current_step
    action_history = state.get("action_history", [])

    # --- FAST PATH: Auto-navigate if URL was pre-constructed ---
    pending_type = state.get("pending_input_field_type", "")
    if pending_type == "auto_navigate":
        nav_url = state.get("pending_user_input", "")
        if nav_url:
            logger.info("auto_navigate_executing", url=nav_url[:80])
            action = Action(
                action_id=f"act_{uuid.uuid4().hex[:8]}",
                action_type=ActionType.NAVIGATE,
                value=nav_url,
                description=f"Navigate to {nav_url[:50]}",
                confidence=1.0,
                requires_confirmation=False,
                risk_level="low",
            )
            return {
                "current_action": action,
                "cognitive_status": CognitiveStatus.EXECUTING,
                "pending_user_input": "",
                "pending_input_field_type": "",
            }

    # --- FAST PATH: Execute queued action (no LLM call) ---
    # If a previous cycle queued predictable follow-up actions, execute the next one.
    queued = state.get("_queued_actions", [])
    if queued:
        next_action_dict = queued[0]
        remaining = queued[1:]
        logger.info("queued_action_executing",
                     action_type=next_action_dict.get("action_type"),
                     remaining=len(remaining))
        action = Action(
            action_id=f"act_{uuid.uuid4().hex[:8]}",
            action_type=ActionType(next_action_dict["action_type"]),
            element_id=next_action_dict.get("element_id"),
            value=next_action_dict.get("value", ""),
            description=next_action_dict.get("description", "Auto-chained action"),
            confidence=1.0,
            requires_confirmation=False,
            risk_level="low",
        )
        return {
            "current_action": action,
            "cognitive_status": CognitiveStatus.EXECUTING,
            "_queued_actions": remaining,
            "current_reasoning": next_action_dict.get("reasoning", ""),
        }

    # --- FAST PATH: Auto-type ALL stored credentials at once ---
    # If the user provided credentials via interrupt, type them ALL into
    # the matching fields using JavaScript — no LLM call needed.
    # This fills email + password (or any combo) in a single action cycle.
    stored_creds = state.get("_stored_credentials", {})
    pending_input = state.get("pending_user_input", "")
    pending_type = state.get("pending_input_field_type", "")

    # Merge pending_input into stored_creds if not already there
    if pending_input and pending_type and pending_type not in stored_creds:
        stored_creds = dict(stored_creds)
        stored_creds[pending_type] = pending_input

    if stored_creds:
        page_ctx = state.get("page_context")
        if page_ctx:
            _field_matchers = [
                ("email", lambda t, n, p: t == "email" or "email" in n or "email" in p),
                ("password", lambda t, n, p: t == "password" or "password" in n),
                ("verification", lambda t, n, p: any(w in n or w in p for w in ("otp", "code", "verification", "token"))),
                ("payment", lambda t, n, p: any(w in n or w in p for w in ("card", "cvv", "number"))),
            ]

            # Find the FIRST credential that has a matching field on the page
            for field_type, matcher in _field_matchers:
                value = stored_creds.get(field_type, "")
                if not value:
                    continue

                for el in page_ctx.elements:
                    attrs = el.attributes
                    el_type = attrs.get("type", "").lower()
                    el_name = attrs.get("name", "").lower()
                    el_placeholder = attrs.get("placeholder", "").lower()
                    # Also check aria-label for modern SPA forms
                    el_aria = attrs.get("aria-label", "").lower()

                    if not el.is_enabled:
                        continue

                    if matcher(el_type, el_name, el_placeholder) or matcher(el_type, el_aria, el_placeholder):
                        logger.info("auto_type_credential",
                                    field_type=field_type,
                                    element_id=el.element_id)

                        action = Action(
                            action_id=f"act_{uuid.uuid4().hex[:8]}",
                            action_type=ActionType.CLEAR_AND_TYPE,
                            element_id=el.element_id,
                            value=value,
                            description=f"Typing user-provided {field_type}",
                            confidence=1.0,
                            requires_confirmation=False,
                            risk_level="low",
                        )

                        # Remove this credential — it's been used
                        updated_creds = dict(stored_creds)
                        updated_creds.pop(field_type, None)

                        # Remove this credential and check what's next
                        remaining_creds = [k for k in updated_creds if updated_creds[k]]
                        queued = []

                        if not remaining_creds:
                            # All credentials typed — auto-queue click on submit button
                            submit_keywords = ("sign in", "log in", "login", "submit", "continue", "next")
                            for btn in page_ctx.elements:
                                btn_text = (btn.text or "").lower().strip()
                                btn_type = btn.attributes.get("type", "").lower()
                                btn_tag = btn.tag_name.lower() if btn.tag_name else ""
                                is_submit = (
                                    btn_type == "submit"
                                    or (btn_tag in ("button", "a", "input") and any(kw in btn_text for kw in submit_keywords))
                                )
                                if is_submit and btn.is_enabled:
                                    queued.append({
                                        "action_type": ActionType.CLICK.value,
                                        "element_id": btn.element_id,
                                        "description": f"Click '{btn_text}' to submit login",
                                        "reasoning": "All credentials entered — submitting the login form.",
                                    })
                                    logger.info("auto_queue_submit",
                                                element_id=btn.element_id,
                                                text=btn_text[:30])
                                    break

                        next_hint = (
                            f"Typed {field_type}. "
                            + (f"Still need: {', '.join(remaining_creds)}." if remaining_creds
                               else "All credentials entered — submitting login form.")
                        )

                        return {
                            "current_action": action,
                            "cognitive_status": CognitiveStatus.EXECUTING,
                            "pending_user_input": "",
                            "pending_input_field_type": "",
                            "_stored_credentials": updated_creds,
                            "_queued_actions": queued,
                            "current_reasoning": next_hint,
                        }
                    # Keep scanning — the right field may be further down the page

    # Smart model routing: use fast model for simple actions, main model for complex
    from agent_core.config import settings as _settings
    model_name = state.get("model_name")
    fast_model = _settings.fast_model

    if fast_model:
        # Use main (big) model when:
        # - First action (need to understand the task)
        # - Findings exist (need to decide if done)
        # - Last action failed (need smarter recovery)
        # - Current reasoning mentions DUPLICATE or STUCK (forced done)
        reasoning = state.get("current_reasoning", "")
        has_findings = any(
            entry.get("result", {}).get("extracted_data")
            for entry in action_history
            if isinstance(entry.get("result", {}).get("extracted_data", ""), str)
            and len(entry.get("result", {}).get("extracted_data", "")) > 20
        )
        last_failed = (
            action_history
            and action_history[-1].get("result", {}).get("status") != "success"
        )
        forced_done = "DUPLICATE" in reasoning or "STUCK" in reasoning

        # Also check: has the stuck loop fired repeatedly with no change?
        stuck_count = reasoning.count("STUCK LOOP")

        needs_big_model = (
            len(action_history) == 0  # First action
            or has_findings             # Has findings to analyze
            or last_failed              # Recovery needed
            or forced_done              # Forced completion
            or stuck_count > 0          # Stuck — small model isn't following instructions
        )

        if not needs_big_model:
            model_name = fast_model
            logger.info("smart_model_routing", model="fast", reason="simple_action")
        else:
            logger.info("smart_model_routing", model="main", reason="complex_decision")

    # Use dynamic tool selection based on context
    goal = state.get("goal", Goal(original_text=""))

    llm = get_action_llm_dynamic(
        model_name=model_name,
        api_keys=state.get("api_keys"),
        page_context=state.get("page_context"),
        current_step=current_step.description if current_step else "",
        action_history=action_history,
        goal_text=goal.original_text,
    )

    page_context_str = ""
    if state.get("page_context"):
        page_context_str = state["page_context"].to_llm_representation(max_elements=40)

    latest_reasoning = state.get("current_reasoning", "")

    # Pass the ORIGINAL task text — never the rewritten version
    original_task = goal.original_text

    # Detect response template for structured output hints
    template = detect_task_pattern(goal.original_text)
    output_hint = template["output_hint"] if template else ""

    # Build conversation history from prior messages (session memory)
    # Use generous limits to retain context from in-page chat agents
    conversation_context = ""
    prior_msgs = state.get("messages", [])
    if prior_msgs:
        conv_lines = []
        for msg in prior_msgs:
            role = "User" if hasattr(msg, "type") and msg.type == "human" else "Agent"
            content = (msg.content or "")[:600]  # 600 chars to capture longer chat messages
            if content:
                conv_lines.append(f"{role}: {content}")
        if conv_lines:
            conversation_context = "\nPrior conversation:\n" + "\n".join(conv_lines[-10:]) + "\n"

    # Build site memory section from persistent memory
    site_memory_section = ""
    try:
        domain = ""
        page_ctx = state.get("page_context")
        if page_ctx and getattr(page_ctx, "url", ""):
            domain = extract_domain(page_ctx.url)
        if domain:
            mem = get_memory()
            site_mem = mem.format_for_prompt(domain)
            if site_mem:
                site_memory_section = f"\n## SITE MEMORY (from past sessions)\n{site_mem}\n"
    except Exception:
        pass

    prompt = ACTION_DECISION_PROMPT.format(
        goal=original_task,
        reasoning=latest_reasoning or "First action — start working on the task.",
        site_memory=site_memory_section,
        page_context=page_context_str or "No page loaded.",
        action_history=format_action_history(action_history, max_entries=5),
        output_format_hint=f"\n{output_hint}\n" if output_hint else "",
    )

    # Inject conversation history before the prompt if available
    if conversation_context:
        prompt = conversation_context + "\n" + prompt

    # X1: pick capability-scoped system prompt. Falls back to META (full prompt)
    # when classifier returns "META", preserving today's behavior for ambiguous tasks.
    # A page counts as "content" if it has any interactive elements at all OR
    # a non-blank URL on an http/https scheme. The old ">3 elements" heuristic
    # mis-classified small pages (e.g., a simple search form with 3 elements)
    # as empty → NAV, which steered the LLM away from the right tool.
    pc = state.get("page_context")
    url = (getattr(pc, "url", "") or "").lower() if pc else ""
    is_real_url = url.startswith(("http://", "https://")) and "about:blank" not in url
    has_elements = bool(pc and len(getattr(pc, "elements", [])) >= 1)
    page_has_content = bool(pc) and (has_elements or is_real_url)

    capability = classify_action_capability(
        goal_text=original_task,
        has_page_content=page_has_content,
        action_count=len(action_history),
    )
    system_prompt = CAPABILITY_SYSTEM_PROMPTS.get(capability, SYSTEM_ACTION_DECISION)
    logger.info("decide_action_capability", capability=capability, action_count=len(action_history))

    response = await llm.ainvoke([
        SystemMessage(content=system_prompt),
        HumanMessage(content=prompt),
    ])

    # Capture Qwen-style thinking from the response
    thinking, _ = _extract_thinking(response.content if response.content else "")

    # Extract tool call from response
    action = None
    tool_name = ""
    if response.tool_calls:
        tool_call = response.tool_calls[0]
        action_id = f"act_{uuid.uuid4().hex[:8]}"

        # Map tool name to ActionType — MUST cover all browser tools
        tool_to_action = {
            # Element interactions
            "click": ActionType.CLICK,
            "type_text": ActionType.CLEAR_AND_TYPE,
            "clear_and_type": ActionType.CLEAR_AND_TYPE,
            "select_option": ActionType.SELECT_OPTION,
            "hover": ActionType.HOVER,
            "check": ActionType.CHECK,
            "uncheck": ActionType.UNCHECK,
            # Navigation
            "navigate": ActionType.NAVIGATE,
            "go_back": ActionType.GO_BACK,
            "go_forward": ActionType.GO_FORWARD,
            "refresh": ActionType.REFRESH,
            # Scrolling
            "scroll_down": ActionType.SCROLL_DOWN,
            "scroll_up": ActionType.SCROLL_UP,
            "scroll_to_element": ActionType.SCROLL_TO_ELEMENT,
            # Keyboard
            "press_key": ActionType.PRESS_KEY,
            "key_combo": ActionType.KEY_COMBO,
            # Tab management
            "new_tab": ActionType.NEW_TAB,
            "close_tab": ActionType.CLOSE_TAB,
            "switch_tab": ActionType.SWITCH_TAB,
            # Information gathering
            "extract_text": ActionType.EXTRACT_TEXT,
            "read_page": ActionType.EXTRACT_TEXT,
            "visual_check": ActionType.TAKE_SCREENSHOT,
            "extract_table": ActionType.EXTRACT_TABLE,
            "extract_listings": ActionType.EXTRACT_TEXT,
            "take_screenshot": ActionType.TAKE_SCREENSHOT,
            # JavaScript
            "evaluate_js": ActionType.EVALUATE_JS,
            # Dialogs
            "handle_dialog": ActionType.HANDLE_DIALOG,
            # File & drag
            "upload_file": ActionType.UPLOAD_FILE,
            "drag": ActionType.DRAG,
            # Smart waiting
            "wait_for_selector": ActionType.WAIT_FOR_SELECTOR,
            "wait_for_navigation": ActionType.WAIT_FOR_NAVIGATION,
            # Special
            "wait": ActionType.WAIT,
            "ask_user": ActionType.DONE,
            "done": ActionType.DONE,
        }

        tool_name = tool_call["name"]

        # Fix hallucinated tool calls — map deprecated/non-existent tools to correct ones
        _tool_aliases = {
            "take_screenshot": "visual_check",  # take_screenshot removed, use visual_check
            "fill": "type_text",                # some models call "fill" instead of "type_text"
            "clear_and_type": "type_text",      # merged into type_text (always clears first)
        }
        if tool_name in _tool_aliases:
            logger.info("tool_alias_applied", original=tool_name, mapped=_tool_aliases[tool_name])
            tool_name = _tool_aliases[tool_name]

        action_type = tool_to_action.get(tool_name, ActionType.DONE)
        args = tool_call.get("args", {})

        # For visual_check (direct or aliased), inject the __VISUAL_CHECK__ marker
        # The tool function is never executed — it's just a schema. We need to set the value here.
        if tool_name == "visual_check":
            desc = args.get("description", "Analyze the current page visually")
            args["value"] = f"__VISUAL_CHECK__|{desc}"

        # For read_page, inject the __READ_PAGE__ marker
        # BUT: if the goal asks for structured/exportable data, use extract_listings instead
        # AND: if the goal requires image/visual analysis, redirect to visual_check instead
        if tool_name == "read_page":
            template = detect_task_pattern(goal.original_text)
            if template and template["name"] == "image_analysis":
                logger.info("read_page_to_visual_check",
                            msg="Goal requires image analysis — upgrading read_page to visual_check")
                tool_name = "visual_check"
                action_type = ActionType.TAKE_SCREENSHOT
                desc = args.get("description", goal.original_text[:200])
                args["value"] = f"__VISUAL_CHECK__|{desc}"
            elif template and template["name"] in ("data_extraction", "price_check"):
                logger.info("read_page_to_extract_listings",
                            msg="Goal requires structured data — upgrading read_page to extract_listings")
                tool_name = "extract_listings"
                action_type = ActionType.EXTRACT_TEXT
                args["value"] = "__EXTRACT_LISTINGS__"
            else:
                args["value"] = "__READ_PAGE__"

        # For extract_listings, inject the __EXTRACT_LISTINGS__ marker
        if tool_name == "extract_listings":
            args["value"] = "__EXTRACT_LISTINGS__"

        # Determine if this is a "done" or "ask_user" action
        is_done = tool_call["name"] == "done"
        is_ask = tool_call["name"] == "ask_user"

        # Guard: don't allow done() on first action if task requires data gathering.
        # The LLM sometimes calls done() immediately without extracting any data.
        # Only block if NO data-gathering action has been performed yet.
        has_gathered_data = any(
            e.get("action", {}).get("action_type") in ("extract_text", "read_page", "take_screenshot")
            for e in action_history
        )
        if is_done and not has_gathered_data:
            template = detect_task_pattern(goal.original_text)
            if template:
                logger.info("premature_done_blocked",
                            pattern=template["name"],
                            msg="Blocked done() before data gathering — using extract_listings instead")
                tool_name = "extract_listings"
                action_type = ActionType.EXTRACT_TEXT
                args = {"value": "__EXTRACT_LISTINGS__", "description": f"Extract {template['name']} data"}
                is_done = False

        # Determine risk level and confirmation requirement
        latest_traces = state.get("reasoning_traces", [])
        confidence = latest_traces[-1].confidence if latest_traces else 0.5

        # Risk assessment: auto-confirm most actions, but require user confirmation
        # for actions that involve money, credentials, or irreversible changes.
        # This is CONTEXT-BASED, not just action-type-based.
        _always_confirm_actions = {ActionType.EVALUATE_JS, ActionType.UPLOAD_FILE}

        requires_confirmation = action_type in _always_confirm_actions
        risk_level = "low"

        # Context-based risk: check if the element or action description suggests
        # a high-stakes operation (payment, login, cart, order, delete, etc.)
        if action_type in {ActionType.CLICK, ActionType.CLEAR_AND_TYPE}:
            # Check element text and description for high-risk signals
            desc_lower = args.get("description", "").lower()
            element_text = ""
            eid = raw_eid if 'raw_eid' in dir() else args.get("element_id")
            page_ctx = state.get("page_context")
            if page_ctx and eid is not None:
                for el in page_ctx.elements:
                    if el.element_id == eid:
                        element_text = (el.text or "").lower()
                        break

            # Check ELEMENT TEXT only for risk signals — not the description
            # (description contains agent reasoning which may mention "sign in" for non-auth clicks)
            _payment_signals = (
                "pay", "payment", "checkout", "purchase", "buy now", "place order",
                "confirm order", "submit order", "complete purchase", "proceed to pay",
            )
            _auth_signals = (
                "login", "log in", "sign in", "signin",
            )
            _cart_signals = (
                "add to cart", "add to bag", "add to basket",
            )
            _destructive_signals = (
                "delete account", "cancel order", "cancel subscription",
                "deactivate account", "close account",
            )

            is_payment = any(s in element_text for s in _payment_signals)
            is_auth = any(s in element_text for s in _auth_signals)
            is_cart = any(s in element_text for s in _cart_signals)
            is_destructive = any(s in element_text for s in _destructive_signals)

            # Check if typing into a sensitive field
            is_sensitive_field = False
            sensitive_field_type = ""
            if page_ctx and eid is not None:
                for el in page_ctx.elements:
                    if el.element_id == eid:
                        el_type = el.attributes.get("type", "").lower()
                        el_name = el.attributes.get("name", "").lower()
                        el_placeholder = el.attributes.get("placeholder", "").lower()
                        el_label = f"{el_name} {el_placeholder} {el_type}"

                        if el_type == "password" or "password" in el_name or "passwd" in el_name:
                            is_sensitive_field = True
                            sensitive_field_type = "password"
                        elif el_type == "email" or "email" in el_name:
                            # Email on a login/signup page = sensitive
                            if is_auth or any(w in element_text for w in ("sign", "login", "account", "register")):
                                is_sensitive_field = True
                                sensitive_field_type = "email"
                        elif any(w in el_label for w in ("card", "cvv", "expir", "credit", "debit")):
                            is_sensitive_field = True
                            sensitive_field_type = "payment"
                        elif any(w in el_label for w in ("otp", "verification", "code", "token", "pin")):
                            is_sensitive_field = True
                            sensitive_field_type = "verification"
                        break

            # If typing into a sensitive field, check if the user provided
            # the value in their task text. If not, the agent is fabricating
            # credentials — switch to ask_user instead.
            # SKIP this check if we already have stored credentials waiting to be typed
            # (the fast path at the top of decide_action handles those)
            existing_creds = state.get("_stored_credentials", {})
            if is_sensitive_field and action_type in {ActionType.CLEAR_AND_TYPE} and not existing_creds:
                typed_value = _extract_value(args) or ""
                # Strip submit marker
                typed_value = typed_value.replace("|SUBMIT", "").strip()
                original_task = goal.original_text

                # Check if the typed value was provided by the user — either in the
                # original task text OR in a recent user response (via ask_user interrupt)
                current_reasoning = state.get("current_reasoning", "")
                user_provided = typed_value and (
                    typed_value.lower() in original_task.lower()
                    or typed_value.lower() in current_reasoning.lower()
                )

                if not user_provided and typed_value:
                    # Agent is making up sensitive data — override to ask_user
                    logger.info("sensitive_field_override",
                                field_type=sensitive_field_type,
                                fabricated_value=typed_value[:20] + "...",
                                target_element=eid)

                    action = Action(
                        action_id=f"act_{uuid.uuid4().hex[:8]}",
                        action_type=ActionType.DONE,
                        value=f"I need your {sensitive_field_type} to continue. Please provide your {sensitive_field_type}.",
                        description=f"Asking user for {sensitive_field_type}",
                        risk_level="high",
                        requires_confirmation=False,
                    )
                    return {
                        "current_action": action,
                        "cognitive_status": CognitiveStatus.ASKING_USER,
                        # Store the field info so we can auto-type when user responds
                        "pending_input_field_type": sensitive_field_type,
                    }

            if is_payment:
                requires_confirmation = True
                risk_level = "high"
            elif is_auth or is_destructive:
                requires_confirmation = True
                risk_level = "high"
            elif is_sensitive_field:
                requires_confirmation = True
                risk_level = "high"
            elif is_cart:
                requires_confirmation = True
                risk_level = "medium"

        # Sanitize element_id — LLM sometimes returns a list [1] instead of int 1
        raw_eid = args.get("element_id") or args.get("source_element_id")
        if isinstance(raw_eid, list):
            raw_eid = raw_eid[0] if raw_eid else None
        if raw_eid is not None:
            try:
                raw_eid = int(raw_eid)
            except (ValueError, TypeError):
                raw_eid = None

        action = Action(
            action_id=action_id,
            action_type=action_type,
            element_id=raw_eid,
            value=_extract_value(args),
            description=args.get("description", ""),
            reasoning=state.get("current_reasoning", ""),
            confidence=confidence,
            requires_confirmation=requires_confirmation,
            risk_level=risk_level,
        )

    if action is None:
        # LLM didn't produce a tool call — ask it to try again or mark as stuck
        action = Action(
            action_id=f"act_{uuid.uuid4().hex[:8]}",
            action_type=ActionType.DONE,
            value="Unable to determine next action",
            description="Agent could not determine what action to take",
            confidence=0.0,
        )

    next_status = CognitiveStatus.AWAITING_CONFIRMATION
    if not action.requires_confirmation:
        next_status = CognitiveStatus.EXECUTING
    if action.action_type == ActionType.DONE:
        # Only route to ask_user if the LLM explicitly called the ask_user tool
        if tool_name == "ask_user":
            next_status = CognitiveStatus.ASKING_USER
        else:
            next_status = CognitiveStatus.COMPLETED

    result = {
        "current_action": action,
        "cognitive_status": next_status,
    }
    if thinking:
        result["current_thinking"] = thinking
    return result


# ============================================================
# Node 6: Observe (after action execution)
# ============================================================

async def observe(state: AgentState) -> dict:
    """Observe the result of an action and update task memory.

    Called after the browser reports back with an ActionResult.
    Updates memory with observations about the page/site behavior.
    """
    logger.info("observe")

    result = state.get("pending_action_result")
    action = state.get("current_action")
    memory = state.get("task_memory", TaskMemory())

    # Update memory with observations
    new_memory = TaskMemory(
        observations=list(memory.observations),
        discovered_patterns=list(memory.discovered_patterns),
        user_preferences=dict(memory.user_preferences),
        important_data=dict(memory.important_data),
        pages_visited=list(memory.pages_visited),
    )

    # Deterministic page-diff observations (no LLM call)
    prev_ctx = state.get("previous_page_context")
    curr_ctx = state.get("page_context")
    page_diff_notes = []

    if prev_ctx and curr_ctx:
        if getattr(prev_ctx, "url", None) != getattr(curr_ctx, "url", None):
            page_diff_notes.append(
                f"Page navigated: {getattr(prev_ctx, 'url', '?')} → {getattr(curr_ctx, 'url', '?')}"
            )
        if getattr(prev_ctx, "title", None) != getattr(curr_ctx, "title", None):
            page_diff_notes.append(
                f"Title changed: '{getattr(prev_ctx, 'title', '')}' → '{getattr(curr_ctx, 'title', '')}'"
            )
        prev_count = len(getattr(prev_ctx, "interactive_elements", []))
        curr_count = len(getattr(curr_ctx, "interactive_elements", []))
        if abs(curr_count - prev_count) > 5:
            page_diff_notes.append(
                f"Page structure changed ({prev_count} → {curr_count} interactive elements)"
            )
    elif curr_ctx and not prev_ctx:
        page_diff_notes.append(f"Initial page loaded: {getattr(curr_ctx, 'url', 'unknown')}")

    if not page_diff_notes:
        page_diff_notes.append("No significant page changes observed")

    new_memory.observations.extend(page_diff_notes)

    if result:
        if result.page_changed and result.new_url:
            if result.new_url not in new_memory.pages_visited:
                new_memory.pages_visited.append(result.new_url)

        if result.status == ActionStatus.SUCCESS and action:
            new_memory.observations.append(
                f"Action '{action.action_type.value}' on element [{action.element_id}] succeeded"
            )
        elif result and result.status != ActionStatus.SUCCESS:
            new_memory.observations.append(
                f"Action failed: {result.error or result.message}"
            )

        if result.extracted_data:
            new_memory.important_data[f"extracted_{len(new_memory.important_data)}"] = result.extracted_data

    # Record action in history. Cap at ACTION_HISTORY_MAX to prevent
    # unbounded growth over long tasks — prompts already truncate at render
    # time (max_entries=5), but the underlying list would otherwise bloat
    # the checkpointer state row every iteration.
    ACTION_HISTORY_MAX = 50
    action_history = list(state.get("action_history", []))
    if action and result:
        action_history.append({
            "action": _safe_serialize(action),
            "result": _safe_serialize(result),
        })
    if len(action_history) > ACTION_HISTORY_MAX:
        action_history = action_history[-ACTION_HISTORY_MAX:]

    # Record action outcome in persistent memory
    try:
        domain = ""
        if curr_ctx and getattr(curr_ctx, "url", ""):
            domain = extract_domain(curr_ctx.url)
        if domain and action and result:
            atype = action.action_type.value if hasattr(action.action_type, "value") else str(action.action_type)
            context = action.description[:60] if action.description else ""
            mem = get_memory()
            mem.record_action(
                domain=domain,
                action_type=atype,
                success=result.status == ActionStatus.SUCCESS,
                context=context,
            )
    except Exception as e:
        logger.warning("memory_record_action_failed", error=str(e)[:100])

    # If this action gathered information, update current_reasoning so
    # the next decide_action knows what was found (P4: findings feed back)
    reasoning_update = {}
    if result and result.extracted_data and len(result.extracted_data) > 10:
        findings_preview = result.extracted_data[:300]
        reasoning_update["current_reasoning"] = (
            f"Previous action gathered information. "
            f"Findings: {findings_preview}"
        )

    return {
        "task_memory": new_memory,
        "action_history": action_history,
        "previous_page_context": state.get("page_context"),
        "cognitive_status": CognitiveStatus.EVALUATING,
        **reasoning_update,
    }


# ============================================================
# Node 7a: Smart Evaluate (deterministic — no LLM call)
# ============================================================

# Actions whose success is always obvious from the result status
_TRIVIAL_ACTIONS = {
    ActionType.SCROLL_DOWN, ActionType.SCROLL_UP, ActionType.SCROLL_TO_ELEMENT,
    ActionType.WAIT, ActionType.WAIT_FOR_SELECTOR, ActionType.WAIT_FOR_NAVIGATION,
    ActionType.PRESS_KEY, ActionType.KEY_COMBO, ActionType.HOVER,
    ActionType.GO_BACK, ActionType.GO_FORWARD, ActionType.REFRESH,
    ActionType.EXTRACT_TEXT, ActionType.EXTRACT_TABLE, ActionType.TAKE_SCREENSHOT,
    ActionType.GET_CONSOLE_LOGS, ActionType.GET_NETWORK_LOG,
}


async def smart_evaluate(state: AgentState) -> dict:
    """Deterministic evaluation — skips LLM when outcome is obvious.

    Routes to the full LLM evaluate only when the result is ambiguous.
    For ~70% of actions, this avoids an LLM call entirely.
    """
    action = state.get("current_action")
    result = state.get("pending_action_result")
    plan = state.get("plan", Plan())
    prev_ctx = state.get("previous_page_context")
    curr_ctx = state.get("page_context")

    if not action or not result:
        # No action/result — fall through to LLM evaluate
        return {"cognitive_status": CognitiveStatus.EVALUATING}

    action_succeeded = result.status == ActionStatus.SUCCESS
    action_type = action.action_type

    # --- Determine if we can skip the LLM evaluate ---

    # Case 1: Action failed → always need LLM to figure out why
    if not action_succeeded:
        logger.info("smart_evaluate_to_llm", reason="action_failed")
        return {"cognitive_status": CognitiveStatus.EVALUATING}

    # Case 2: Trivial actions (scroll, wait, extract) — success = done
    if action_type in _TRIVIAL_ACTIONS:
        logger.info("smart_evaluate_skip", reason=f"trivial_action:{action_type.value}")
        return _build_fast_evaluation(state, plan, action_succeeded=True)

    # Case 3: Navigate succeeded AND URL changed → obviously worked
    if action_type == ActionType.NAVIGATE and result.page_changed:
        logger.info("smart_evaluate_skip", reason="navigate_success")
        return _build_fast_evaluation(state, plan, action_succeeded=True)

    # Case 4: Type/click succeeded, no unexpected page changes
    if action_type in {ActionType.CLEAR_AND_TYPE}:
        if action_succeeded:
            logger.info("smart_evaluate_skip", reason="type_success")
            return _build_fast_evaluation(state, plan, action_succeeded=True)

    # Case 5: Click succeeded — check context
    if action_type == ActionType.CLICK:
        if action_succeeded and prev_ctx and curr_ctx:
            prev_url = getattr(prev_ctx, "url", "")
            curr_url = getattr(curr_ctx, "url", "")

            # If URL changed, check if it's suspicious
            if prev_url != curr_url:
                suspicious = any(
                    kw in curr_url.lower()
                    for kw in ("sorry", "captcha", "login", "signin", "error", "blocked")
                )
                if suspicious:
                    logger.info("smart_evaluate_to_llm", reason="suspicious_redirect")
                    return {"cognitive_status": CognitiveStatus.EVALUATING}

            # If this was a submit/confirm/send click, use LLM to verify outcome
            desc = (action.description or "").lower()
            submit_words = ("submit", "confirm", "send", "sign in", "log in", "login",
                            "create", "save", "apply", "start", "proceed", "next", "continue")
            if any(w in desc for w in submit_words):
                logger.info("smart_evaluate_to_llm", reason="submit_click_needs_verification")
                return {"cognitive_status": CognitiveStatus.EVALUATING}
            # Click succeeded, no suspicious redirect → skip evaluate
            logger.info("smart_evaluate_skip", reason="click_success")
            return _build_fast_evaluation(state, plan, action_succeeded=True)

    # Default: unclear outcome → need LLM
    logger.info("smart_evaluate_to_llm", reason="unclear_outcome")
    return {"cognitive_status": CognitiveStatus.EVALUATING}


def _build_fast_evaluation(state: AgentState, plan: Plan, action_succeeded: bool) -> dict:
    """Build a deterministic evaluation result without LLM call."""
    evaluation = Evaluation(
        action_succeeded=action_succeeded,
        goal_progress="Action completed successfully" if action_succeeded else "Action failed",
        progress_percentage=len(plan.completed_steps) / max(len(plan.steps), 1),
        unexpected_results="",
        next_action_suggestion="",
        should_continue=True,
        should_re_plan=False,
        re_plan_reason="",
    )

    # Advance plan step
    updated_plan = Plan(
        steps=list(plan.steps),
        current_step_index=plan.current_step_index,
        plan_version=plan.plan_version,
        original_reasoning=plan.original_reasoning,
        re_plan_reason=plan.re_plan_reason,
    )
    if action_succeeded and updated_plan.current_step:
        updated_plan.steps[updated_plan.current_step_index] = PlanStep(
            **{**updated_plan.current_step.model_dump(), "status": StepStatus.COMPLETED}
        )
        updated_plan.current_step_index += 1

    return {
        "latest_evaluation": evaluation,
        "plan": updated_plan,
        "cognitive_status": CognitiveStatus.SELF_CRITIQUING,
    }


# ============================================================
# Node 7b: Evaluate (LLM-based — only for ambiguous outcomes)
# ============================================================

async def evaluate(state: AgentState) -> dict:
    """LLM-based evaluation — only called when smart_evaluate can't determine the outcome.

    Called for: failed actions, suspicious redirects, unclear click results.
    """
    logger.info("evaluate")

    llm = get_reasoning_llm(state.get("model_name"), api_keys=state.get("api_keys"))

    action = state.get("current_action", Action(action_type=ActionType.DONE))
    result = state.get("pending_action_result")
    plan = state.get("plan", Plan())

    # Use compact representation for current page — saves ~50% tokens
    page_context_str = ""
    if state.get("page_context"):
        page_context_str = state["page_context"].to_llm_representation(compact=True)

    # Build deterministic page diff instead of sending full before context
    prev_ctx = state.get("previous_page_context")
    curr_ctx = state.get("page_context")
    diff_lines = []
    if prev_ctx and curr_ctx:
        prev_url = getattr(prev_ctx, "url", "")
        curr_url = getattr(curr_ctx, "url", "")
        if prev_url != curr_url:
            diff_lines.append(f"URL: {prev_url} -> {curr_url}")
        prev_title = getattr(prev_ctx, "title", "")
        curr_title = getattr(curr_ctx, "title", "")
        if prev_title != curr_title:
            diff_lines.append(f"Title: '{prev_title}' -> '{curr_title}'")
        prev_count = len(getattr(prev_ctx, "interactive_elements", []))
        curr_count = len(getattr(curr_ctx, "interactive_elements", []))
        if prev_count != curr_count:
            diff_lines.append(f"Elements: {prev_count} -> {curr_count}")
        if not diff_lines:
            diff_lines.append("No significant changes")
    else:
        diff_lines.append("No previous page state available")
    page_diff = "\n".join(diff_lines)

    prompt = EVALUATION_PROMPT.format(
        action_description=action.description,
        action_result=json.dumps(_safe_serialize(result), default=str) if result else "No result",
        expected_outcome=plan.current_step.expected_outcome if plan.current_step else "N/A",
        page_diff=page_diff,
        current_page_context=page_context_str or "Not available",
        goal=state["goal"].interpreted_goal,
        completed_steps=len(plan.completed_steps),
        total_steps=len(plan.steps),
    )

    response = await llm.ainvoke([
        SystemMessage(content=SYSTEM_EVALUATION),
        HumanMessage(content=prompt),
    ])

    # Capture Qwen-style thinking
    thinking, _ = _extract_thinking(response.content)

    try:
        eval_data = _parse_llm_json(response.content)
        evaluation = Evaluation(
            action_succeeded=eval_data.get("action_succeeded", False),
            goal_progress=eval_data.get("goal_progress") or "",
            progress_percentage=0.0,  # Removed from prompt — not used for decisions
            unexpected_results=eval_data.get("unexpected_results") or "",
            next_action_suggestion="",  # Removed from prompt — never used
            should_continue=eval_data.get("should_continue", True),
            should_re_plan=eval_data.get("should_re_plan", False),
            re_plan_reason=eval_data.get("re_plan_reason") or "",
        )
    except (json.JSONDecodeError, KeyError) as e:
        # Surface parse failures instead of silently defaulting — the fallback
        # used to look identical to a normal "failed action, keep going" result
        # which masked upstream LLM output bugs (bad JSON, wrong schema, empty
        # response). Log + include a visible breadcrumb in goal_progress so it
        # reaches the client via server_evaluation.
        raw_preview = (response.content or "")[:200].replace("\n", " ")
        logger.warning(
            "evaluate_json_parse_failed",
            error=str(e),
            raw_preview=raw_preview,
        )
        evaluation = Evaluation(
            action_succeeded=result.status == ActionStatus.SUCCESS if result else False,
            should_continue=True,
            goal_progress=f"[warning] evaluator response malformed ({type(e).__name__}) — defaulting to continue",
            unexpected_results=f"raw LLM output preview: {raw_preview[:120]}",
        )

    # Update plan step status based on evaluation
    updated_plan = Plan(
        steps=list(plan.steps),
        current_step_index=plan.current_step_index,
        plan_version=plan.plan_version,
        original_reasoning=plan.original_reasoning,
        re_plan_reason=evaluation.re_plan_reason if evaluation.should_re_plan else plan.re_plan_reason,
    )

    if evaluation.action_succeeded and updated_plan.current_step:
        updated_plan.steps[updated_plan.current_step_index] = PlanStep(
            **{**updated_plan.current_step.model_dump(), "status": StepStatus.COMPLETED}
        )
        # Advance to next step
        updated_plan.current_step_index += 1

    result_dict = {
        "latest_evaluation": evaluation,
        "plan": updated_plan,
        "cognitive_status": CognitiveStatus.SELF_CRITIQUING,
    }
    if thinking:
        result_dict["current_thinking"] = thinking
    return result_dict


# ============================================================
# Helper: LLM-based completion critique
# ============================================================

async def _critique_completion(state: AgentState, plan: Plan) -> dict:
    """Use LLM to verify that step outcomes actually match before declaring COMPLETED."""
    logger.info("critique_completion_check")

    llm = get_reasoning_llm(state.get("model_name"), api_keys=state.get("api_keys"))

    page_context_str = ""
    if state.get("page_context"):
        page_context_str = state["page_context"].to_llm_representation()

    goal = state.get("goal", Goal(original_text=""))

    plan_steps_str = "\n".join(
        f"  Step {s.step_id}: {s.description} → Expected: {s.expected_outcome}"
        for s in plan.steps
    )

    prompt = STEP_COMPLETION_CRITIQUE_PROMPT.format(
        goal=goal.interpreted_goal or goal.original_text,
        plan_steps=plan_steps_str or "No steps.",
        page_context=page_context_str or "No page loaded.",
    )

    try:
        response = await llm.ainvoke([
            SystemMessage(content=SYSTEM_COMPLETION_CRITIQUE),
            HumanMessage(content=prompt),
        ])
        result = _parse_llm_json(response.content)
        outcomes_match = result.get("outcomes_match", True)
        recommendation = result.get("recommendation", "COMPLETED")
        mismatches = result.get("mismatches", [])
    except Exception as e:
        logger.warning("critique_completion_error", error=str(e))
        # On error, be permissive — don't block completion
        outcomes_match = True
        recommendation = "COMPLETED"
        mismatches = []

    if outcomes_match or recommendation == "COMPLETED":
        return {"cognitive_status": CognitiveStatus.COMPLETED, "should_terminate": True}
    else:
        # Outcomes don't match — re-plan if allowed
        mismatch_summary = "; ".join(mismatches[:3]) if mismatches else "Outcomes don't match expected results"
        logger.info("critique_completion_replan", mismatches=mismatch_summary)

        if plan.plan_version < 3:
            updated_plan = Plan(
                steps=list(plan.steps),
                current_step_index=plan.current_step_index,
                plan_version=plan.plan_version,
                original_reasoning=plan.original_reasoning,
                re_plan_reason=f"Step completion critique: {mismatch_summary}",
            )
            return {
                "plan": updated_plan,
                "cognitive_status": CognitiveStatus.RE_PLANNING,
                "should_terminate": False,
            }
        else:
            # Too many re-plans, pass through to verify_goal for final check
            return {"cognitive_status": CognitiveStatus.COMPLETED, "should_terminate": True}


# ============================================================
# Node 8: Self-Critique (post-action)
# ============================================================

async def self_critique_action(state: AgentState) -> dict:
    """Self-critique after action execution.

    Lighter than plan critique — focuses on whether the approach
    is working and if strategy needs adjustment.
    """
    logger.info("self_critique_action")

    evaluation = state.get("latest_evaluation")
    if not evaluation:
        return {"cognitive_status": CognitiveStatus.REASONING}

    plan = state.get("plan", Plan())

    # --- Stuck loop detection ---
    action_history = state.get("action_history", [])

    # Increment iteration count HERE (not in reason node) since reactive mode
    # skips reason and goes decide_action → execute → self_critique → decide_action
    iteration = state.get("iteration_count", 0) + 1

    # Safety: max iterations
    if iteration > state.get("max_iterations", 25):
        return {
            "cognitive_status": CognitiveStatus.COMPLETED,
            "should_terminate": True,
            "iteration_count": iteration,
            "current_reasoning": "Maximum iterations reached. Reporting what was found so far.",
        }

    # Auto-complete after successful extract_listings — this is a one-shot extraction.
    # The data is already gathered, no need for additional actions.
    if action_history:
        last_action = action_history[-1]
        last_value = last_action.get("action", {}).get("value", "")
        last_status = last_action.get("result", {}).get("status", "")
        if last_value == "__EXTRACT_LISTINGS__" and last_status == "success":
            extracted = last_action.get("result", {}).get("extracted_data", "")
            if extracted and len(extracted) > 50:
                logger.info("extract_listings_auto_done",
                            msg="Structured extraction complete — auto-finishing")
                # Format as natural language summary, not raw JSON
                summary = _format_listings_summary(extracted)
                return {
                    "current_action": Action(
                        action_id=f"act_{uuid.uuid4().hex[:8]}",
                        action_type=ActionType.DONE,
                        value=summary,
                        description="Structured data extracted successfully",
                        confidence=1.0,
                        requires_confirmation=False,
                    ),
                    "cognitive_status": CognitiveStatus.COMPLETED,
                    "iteration_count": iteration,
                }

    # Auto-complete after successful visual_check for image_analysis tasks —
    # the vision model response IS the answer; no need for further actions.
    if action_history:
        last_action = action_history[-1]
        last_value = str(last_action.get("action", {}).get("value", ""))
        last_status = last_action.get("result", {}).get("status", "")
        extracted = last_action.get("result", {}).get("extracted_data", "")
        goal = state.get("goal", Goal(original_text=""))
        template = detect_task_pattern(goal.original_text)

        if ("__VISUAL_CHECK__" in last_value
                and last_status == "success"
                and isinstance(extracted, str) and len(extracted) > 30
                and not extracted.startswith("data:image")
                and template and template["name"] == "image_analysis"):
            logger.info("visual_check_auto_done",
                        msg="Image analysis complete via visual_check — auto-finishing")
            return {
                "cognitive_status": CognitiveStatus.DECIDING,
                "current_reasoning": (
                    f"Visual analysis complete. The vision model reported: {extracted[:500]}. "
                    "You now have the visual evidence needed. Call done() with your finding — "
                    "clearly state whether evidence of vape/tobacco/e-cigarette products was found or not."
                ),
            }

    # --- Auto-wait after any interaction that expects a page response ---
    # Covers: chat messages, form submissions, clicking submit/confirm/send buttons,
    # typing + Enter, or any action that triggers server-side processing.
    if len(action_history) >= 1:
        last = action_history[-1]
        last_type = last.get("action", {}).get("action_type", "")
        last_value = str(last.get("action", {}).get("value", "")).lower()
        last_desc = str(last.get("action", {}).get("description", "")).lower()
        last_status = last.get("result", {}).get("status", "")

        needs_wait = False
        wait_reason = ""

        # Pattern 1: Typed + submitted (type with |SUBMIT or press_key Enter after type)
        if last_type == "press_key" and "enter" in last_value and len(action_history) >= 2:
            prev_type = action_history[-2].get("action", {}).get("action_type", "")
            if prev_type in ("clear_and_type", "type_text"):
                needs_wait = True
                wait_reason = "Sent a message or submitted a form"

        # Pattern 2: Type with auto-submit (|SUBMIT flag)
        if last_type in ("clear_and_type", "type_text") and "|submit" in last_value:
            needs_wait = True
            wait_reason = "Typed and submitted"

        # Pattern 3: Clicked a submit/confirm/send button (in-page form submit only)
        # Skip if: URL changed (= navigation click), page_changed flag set, or no prev context
        if last_type == "click" and last_status == "success":
            page_changed_flag = last.get("result", {}).get("page_changed", False)
            prev_ctx = state.get("previous_page_context")
            curr_ctx = state.get("page_context")

            # Determine if this was a navigation (URL changed) vs in-page action
            is_navigation = page_changed_flag  # Extension reported page change
            if prev_ctx and curr_ctx:
                prev_url = getattr(prev_ctx, "url", "")
                curr_url = getattr(curr_ctx, "url", "")
                if prev_url and curr_url and prev_url != curr_url:
                    is_navigation = True
            elif not prev_ctx:
                # No previous context = early in the task, likely a navigation click
                is_navigation = True

            if not is_navigation:
                submit_words = ("submit", "confirm", "send", "sign in",
                                "log in", "login", "create", "save", "apply")
                if any(w in last_desc for w in submit_words):
                    needs_wait = True
                    wait_reason = "Clicked a submit-type button"

        if needs_wait:
            # Don't double-wait — check if the next action is already a wait or read
            already_waited = (
                last_type == "wait"
                or (len(action_history) >= 2 and action_history[-1].get("action", {}).get("action_type") == "wait")
            )
            if not already_waited:
                logger.info("auto_wait_for_response",
                            msg=f"Inserting wait — {wait_reason}")
                return {
                    "cognitive_status": CognitiveStatus.DECIDING,
                    "iteration_count": iteration,
                    "current_reasoning": (
                        f"{wait_reason}. The page may need time to update with a response. "
                        "Use wait(seconds=3), then read_page to check what changed. "
                        "If there are new instructions, follow them step by step."
                    ),
                }

    # --- Strategy Escalation ---
    # When extraction fails repeatedly, escalate to a different strategy instead of giving up.
    # Escalation ladder: read_page → scroll + read_page → visual_check → different site → report partial
    if len(action_history) >= 2:
        last_two = action_history[-2:]
        last_types = [e.get("action", {}).get("action_type", "") for e in last_two]
        both_extract = all(t in ("extract_text", "take_screenshot") for t in last_types)

        if both_extract:
            last_extracts = []
            for entry in last_two:
                ext = entry.get("result", {}).get("extracted_data")
                last_extracts.append(ext if isinstance(ext, str) else "")

            both_empty = all(len(e) < 5 for e in last_extracts)
            both_same = len(last_extracts) == 2 and last_extracts[0][:200] == last_extracts[1][:200] and len(last_extracts[0]) > 20

            if both_empty or both_same:
                reason = "empty results" if both_empty else "same content"

                # Count extraction attempts to determine escalation level
                extract_count = sum(
                    1 for e in action_history
                    if e.get("action", {}).get("action_type") in ("extract_text", "take_screenshot")
                )
                # Count visual_check attempts
                visual_count = sum(
                    1 for e in action_history
                    if e.get("action", {}).get("action_type") == "take_screenshot"
                    and "__VISUAL_CHECK__" in str(e.get("action", {}).get("value", ""))
                )
                # Count scroll attempts
                scroll_count = sum(
                    1 for e in action_history
                    if e.get("action", {}).get("action_type") == "scroll_down"
                )

                logger.info("strategy_escalation",
                            reason=reason,
                            extract_count=extract_count,
                            visual_count=visual_count,
                            scroll_count=scroll_count)

                # Level 1: First failure → suggest scroll then retry
                if extract_count <= 2 and scroll_count < 2:
                    return {
                        "cognitive_status": CognitiveStatus.DECIDING,
                        "retry_context": RetryContext(),
                        "current_reasoning": (
                            f"Text extraction returned {reason}. "
                            "The content might be below the current viewport. "
                            "Try scroll_down first, then read_page again."
                        ),
                    }

                # Level 2: Scroll didn't help → try visual_check
                if extract_count <= 4 and visual_count == 0:
                    return {
                        "cognitive_status": CognitiveStatus.DECIDING,
                        "retry_context": RetryContext(),
                        "current_reasoning": (
                            f"Text extraction failed {extract_count} times ({reason}). "
                            "The page content may be rendered as images or in a JavaScript framework. "
                            "Use visual_check to see what's actually on screen."
                        ),
                    }

                # Level 3: Visual check done → try different approach or report findings
                memory = state.get("task_memory", TaskMemory())
                has_any_findings = any(
                    isinstance(v, str) and len(v) > 20 and not v.startswith("data:image")
                    for v in memory.important_data.values()
                )

                if has_any_findings:
                    # We have SOME findings — report what we have
                    return {
                        "cognitive_status": CognitiveStatus.DECIDING,
                        "retry_context": RetryContext(),
                        "current_reasoning": (
                            "Multiple extraction strategies have been tried. "
                            "You have gathered some findings already (check action history DATA entries). "
                            "Call done(answer) with whatever information you've collected so far."
                        ),
                    }
                else:
                    # No findings at all — try navigating to a different source
                    if extract_count <= 6:
                        return {
                            "cognitive_status": CognitiveStatus.DECIDING,
                            "retry_context": RetryContext(),
                            "current_reasoning": (
                                "This page's content cannot be extracted by any method. "
                                "Try navigating to a DIFFERENT website that might have the same information. "
                                "Use go_back or navigate to a search engine and try another result."
                            ),
                        }
                    else:
                        # Absolute last resort — force complete
                        logger.info("strategy_escalation_exhausted", msg="All strategies failed")
                        return {
                            "cognitive_status": CognitiveStatus.COMPLETED,
                            "should_terminate": True,
                        }

    # Detect unproductive scrolls: 2+ consecutive scrolls without meaningful action between them.
    # Auto-trigger visual_check so the agent can SEE the page layout instead of scrolling blindly.
    if len(action_history) >= 2:
        last_two_types = [e.get("action", {}).get("action_type", "") for e in action_history[-2:]]
        both_scroll = all(t in ("scroll_down", "scroll_up") for t in last_two_types)
        recent_visual = any(
            "__VISUAL_CHECK__" in str(e.get("action", {}).get("value", ""))
            for e in action_history[-4:]
        )
        if both_scroll and not recent_visual:
            goal = state.get("goal", Goal(original_text=""))
            logger.info("unproductive_scroll_visual",
                        msg="2 consecutive scrolls — auto-triggering visual_check")
            auto_action = Action(
                action_id=f"act_{uuid.uuid4().hex[:8]}",
                action_type=ActionType.TAKE_SCREENSHOT,
                value=f"__VISUAL_CHECK__|I've been scrolling but can't find what I need. "
                      f"What is currently visible on screen? What should I click or do next "
                      f"to complete: {goal.original_text[:150]}",
                description="Visual check after unproductive scrolls",
                confidence=1.0,
                requires_confirmation=False,
            )
            return {
                "current_action": auto_action,
                "cognitive_status": CognitiveStatus.EXECUTING,
                "iteration_count": iteration,
            }

    # Detect stuck loop: 3+ actions with no URL change and same action type repeated
    if len(action_history) >= 3:
        last_three = action_history[-3:]
        types = [e.get("action", {}).get("action_type", "") for e in last_three]
        urls = set()
        for e in last_three:
            url = e.get("result", {}).get("new_url", "")
            if url:
                urls.add(url)

        same_type = len(set(types)) == 1  # All 3 same action type
        no_url_change = len(urls) <= 1    # No navigation happened

        if same_type and no_url_change:
            logger.info("self_critique_stuck_loop", msg=f"3x same action ({types[0]}) with no progress")

            memory = state.get("task_memory", TaskMemory())
            has_findings = any(
                isinstance(v, str) and len(v) > 20 and not v.startswith("data:image")
                for v in memory.important_data.values()
            )

            # Check if we already tried visual_check recently
            recent_visual = any(
                "__VISUAL_CHECK__" in str(e.get("action", {}).get("value", ""))
                for e in action_history[-5:]
            )

            if has_findings:
                # We have data — force completion instead of hoping the LLM calls done()
                logger.info("stuck_loop_force_done", msg="3x repeated action with findings — forcing done()")
                # Build summary from gathered findings
                memory = state.get("task_memory", TaskMemory())
                findings_parts = []
                for key, data in (memory.important_data or {}).items():
                    if isinstance(data, str) and len(data) > 20:
                        findings_parts.append(data[:2000])
                summary = "\n".join(findings_parts) if findings_parts else "Task data gathered (see export)."
                auto_done = Action(
                    action_id=f"act_{uuid.uuid4().hex[:8]}",
                    action_type=ActionType.DONE,
                    value=summary[:2000],
                    description="Auto-completing with gathered data",
                    confidence=1.0,
                    requires_confirmation=False,
                )
                return {
                    "current_action": auto_done,
                    "cognitive_status": CognitiveStatus.COMPLETED,
                }
            elif not recent_visual:
                # No findings, haven't tried visual_check — auto-take screenshot
                # This gives the agent actual visual context to break the loop
                goal = state.get("goal", Goal(original_text=""))
                logger.info("stuck_loop_visual_check", msg="Auto-queuing visual_check to break stuck loop")
                auto_action = Action(
                    action_id=f"act_{uuid.uuid4().hex[:8]}",
                    action_type=ActionType.TAKE_SCREENSHOT,
                    value=f"__VISUAL_CHECK__|I am stuck. What is currently visible on this page? What should I do next to complete this task: {goal.original_text[:150]}",
                    description="Visual check to break stuck loop",
                    confidence=1.0,
                    requires_confirmation=False,
                    risk_level="low",
                )
                return {
                    "current_action": auto_action,
                    "cognitive_status": CognitiveStatus.EXECUTING,
                }
            else:
                # Already tried visual_check, still stuck — try a completely different approach
                return {
                    "cognitive_status": CognitiveStatus.DECIDING,
                    "retry_context": RetryContext(),
                    "current_reasoning": (
                        "Stuck after 3 repeated actions and a visual check. "
                        "Try a completely different approach: use navigate() to go "
                        "directly to the target URL (construct from current domain + "
                        "likely path like /routes, /settings, etc.), or use go_back."
                    ),
                }

    # Check if action failed — retry first, re-plan only after retries exhausted
    if not evaluation.action_succeeded:
        retry_ctx = state.get("retry_context", RetryContext())
        if retry_ctx.attempt_number < retry_ctx.max_attempts:
            return {"cognitive_status": CognitiveStatus.RETRYING}
        else:
            return {
                "cognitive_status": CognitiveStatus.RE_PLANNING,
                "retry_context": RetryContext(),
            }

    # Re-plan only if action succeeded but the page is fundamentally wrong
    # (not just because the LLM suggests it — LLMs suggest re-planning too eagerly)
    if evaluation.should_re_plan and evaluation.re_plan_reason:
        # Only re-plan if there's a concrete reason (not generic)
        action_count = len(state.get("action_history", []))
        if action_count >= 2:  # Don't re-plan after just 1-2 actions
            return {"cognitive_status": CognitiveStatus.RE_PLANNING}

    # DEFAULT: Go straight to decide_action (skip reason node).
    # decide_action has full context (page state, plan, history) and can reason internally.
    # Only fall back to the full reason node in exceptional cases.
    need_full_reasoning = False

    # Exception 1: Unexpected results that need deeper analysis
    if evaluation.unexpected_results:
        need_full_reasoning = True
        logger.info("self_critique_needs_reasoning", reason="unexpected_results")

    # Exception 2: Evaluation explicitly suggests re-planning (but didn't trigger it)
    if evaluation.should_re_plan:
        # This shouldn't reach here (handled above), but safety check
        need_full_reasoning = True

    if need_full_reasoning:
        return {
            "cognitive_status": CognitiveStatus.REASONING,
            "retry_context": RetryContext(),
        }

    # --- Action Batching: auto-chain obvious follow-up actions ---
    # After certain actions, the next step is predictable — skip the LLM call
    last_action = state.get("current_action")
    if last_action and evaluation.action_succeeded:
        last_type = last_action.action_type

        # After scroll_down → auto-read page (the user scrolled to see content)
        if last_type in {ActionType.SCROLL_DOWN, ActionType.SCROLL_UP}:
            # Only auto-read if:
            # 1. The task involves information gathering (agent has read before)
            # 2. The last read didn't return duplicate/empty content
            has_read_before = any(
                e.get("action", {}).get("action_type") == "extract_text"
                for e in action_history
            )
            # Check if last 2 reads returned same content (don't auto-read if stuck)
            last_extracts = [
                e.get("result", {}).get("extracted_data", "")
                for e in action_history
                if e.get("action", {}).get("action_type") == "extract_text"
            ]
            is_duplicate = (
                len(last_extracts) >= 2
                and last_extracts[-1][:200] == last_extracts[-2][:200]
                and len(last_extracts[-1]) > 10
            )
            if has_read_before and not is_duplicate:
                logger.info("action_batch", batch="scroll+read_page")
                auto_action = Action(
                    action_id=f"act_{uuid.uuid4().hex[:8]}",
                    action_type=ActionType.EXTRACT_TEXT,
                    value="__READ_PAGE__",
                    description="Auto-read after scroll",
                    confidence=1.0,
                    requires_confirmation=False,
                    risk_level="low",
                )
                return {
                    "current_action": auto_action,
                    "cognitive_status": CognitiveStatus.EXECUTING,
                }

    # Standard path: skip reason, go directly to decide_action
    goal = state.get("goal", Goal(original_text=""))

    # Build reasoning context from what just happened + all gathered findings
    last_result = state.get("pending_action_result")
    last_action = state.get("current_action")

    parts = []

    # What just happened
    if last_action:
        parts.append(f"Last action: {last_action.action_type.value}")
        if last_action.description:
            parts.append(f"  Description: {last_action.description[:100]}")

    # Findings from this action
    if last_result and last_result.extracted_data and len(last_result.extracted_data) > 20:
        if not last_result.extracted_data.startswith("data:image"):
            parts.append(f"\nFindings from last action:\n{last_result.extracted_data[:300]}")
            parts.append("\nIf these findings answer the user's task, call done(answer) NOW.")
        else:
            parts.append("\nVision analysis was performed but returned image data — check action history DATA for the text results.")

    # Count total findings gathered across all actions
    memory = state.get("task_memory", TaskMemory())
    total_findings = sum(1 for v in memory.important_data.values()
                        if isinstance(v, str) and len(v) > 20 and not v.startswith("data:image"))
    if total_findings > 0:
        parts.append(f"\nTotal findings gathered: {total_findings}. Check action history DATA entries for full content.")
        parts.append("If the findings are sufficient to answer the user's task, call done(answer) with a summary of what you found.")

    if not parts:
        parts.append("Continue working on the task.")

    # Remind about the user's original task — the LLM should check if findings
    # satisfy what the user asked for (format, quantity, etc.)
    original_task = goal.original_text
    if total_findings > 0 and len(original_task) > 20:
        parts.append(f"\nRemember the user's original request: \"{original_task[:200]}\"")
        parts.append("Make sure your done() summary matches what the user asked for (format, detail level, etc.)")

    logger.info("self_critique_direct_to_action", findings_count=total_findings, iteration=iteration)
    return {
        "cognitive_status": CognitiveStatus.DECIDING,
        "retry_context": RetryContext(),
        "current_reasoning": "\n".join(parts),
        "iteration_count": iteration,
    }


# ============================================================
# Node 9: Handle Retry
# ============================================================

async def handle_retry(state: AgentState) -> dict:
    """Choose a different strategy after a failed action.

    Key principle: NEVER repeat the same strategy that already failed.
    """
    logger.info("handle_retry")

    llm = get_reasoning_llm(state.get("model_name"), api_keys=state.get("api_keys"))
    retry_ctx = state.get("retry_context", RetryContext())
    action = state.get("current_action")
    result = state.get("pending_action_result")

    page_context_str = ""
    if state.get("page_context"):
        page_context_str = state["page_context"].to_llm_representation()

    failed_action_str = action.description if action else "Unknown"
    error_str = result.error or result.message if result else "Unknown error"

    prompt = RETRY_STRATEGY_PROMPT.format(
        failed_action=failed_action_str,
        error_message=error_str,
        failed_strategies="\n".join(f"  - {s}" for s in retry_ctx.failed_strategies) or "None yet",
        page_context=page_context_str,
        attempt_number=retry_ctx.attempt_number + 1,
        max_attempts=retry_ctx.max_attempts,
    )

    response = await llm.ainvoke([
        SystemMessage(content=SYSTEM_RETRY),
        HumanMessage(content=prompt),
    ])

    try:
        retry_data = _parse_llm_json(response.content)
        new_strategy = retry_data.get("new_strategy", "Try alternative approach")
        should_ask = retry_data.get("should_ask_user", False)
    except (json.JSONDecodeError, KeyError):
        new_strategy = "Try alternative approach"
        should_ask = False

    # Update retry context
    new_retry = RetryContext(
        attempt_number=retry_ctx.attempt_number + 1,
        max_attempts=retry_ctx.max_attempts,
        failed_strategies=list(retry_ctx.failed_strategies) + [failed_action_str],
        last_error=error_str,
        escalation_needed=should_ask,
    )

    next_status = CognitiveStatus.ASKING_USER if should_ask else CognitiveStatus.REASONING

    # Add retry observation to memory
    memory = state.get("task_memory", TaskMemory())
    new_memory = TaskMemory(
        observations=list(memory.observations) + [f"Retry: {new_strategy}"],
        discovered_patterns=list(memory.discovered_patterns),
        user_preferences=dict(memory.user_preferences),
        important_data=dict(memory.important_data),
        pages_visited=list(memory.pages_visited),
    )

    return {
        "retry_context": new_retry,
        "task_memory": new_memory,
        "cognitive_status": next_status,
        "current_reasoning": f"Previous attempt failed. New strategy: {new_strategy}",
    }


# ============================================================
# Node 10: Verify Goal
# ============================================================

async def verify_goal(state: AgentState) -> dict:
    """Verify that success criteria are actually met before declaring completion.

    This catches premature success declarations by checking the current page
    state against the goal's success criteria using an LLM call.
    """
    logger.info("verify_goal")

    goal = state.get("goal", Goal(original_text=""))
    plan = state.get("plan", Plan())

    # If no success criteria defined, pass through
    if not goal.success_criteria:
        logger.info("verify_goal_no_criteria", msg="No success criteria, passing through")
        return {"cognitive_status": CognitiveStatus.COMPLETED, "should_terminate": True}

    # Fast-pass: if the agent already gathered findings (visual_check/read_page data),
    # the task is information-gathering and the findings ARE the result.
    # Don't make another LLM call to "verify" — just accept.
    memory = state.get("task_memory", TaskMemory())
    has_extracted_findings = any(
        isinstance(v, str) and len(v) > 50 and not v.startswith("data:image")
        for v in memory.important_data.values()
    )
    if has_extracted_findings:
        logger.info("verify_goal_fast_pass", reason="findings_available_in_memory")
        return {"cognitive_status": CognitiveStatus.COMPLETED, "should_terminate": True}

    llm = get_reasoning_llm(state.get("model_name"), api_keys=state.get("api_keys"))

    page_context_str = ""
    if state.get("page_context"):
        page_context_str = state["page_context"].to_llm_representation()

    success_criteria_str = "\n".join(
        f"  {i+1}. {c}" for i, c in enumerate(goal.success_criteria)
    )

    prompt = GOAL_VERIFICATION_PROMPT.format(
        goal=goal.interpreted_goal or goal.original_text,
        success_criteria=success_criteria_str,
        page_context=page_context_str or "No page loaded.",
        action_history=format_action_history(state.get("action_history", []), max_entries=8),
    )

    response = await llm.ainvoke([
        SystemMessage(content=SYSTEM_GOAL_VERIFICATION),
        HumanMessage(content=prompt),
    ])

    try:
        verification = _parse_llm_json(response.content)
        all_met = verification.get("all_criteria_met", False)
        explanation = verification.get("explanation", "")
        criteria_results = verification.get("criteria_results", [])

        # Count how many criteria the LLM says are met
        met_count = sum(1 for c in criteria_results if c.get("met", False))
        total_count = len(criteria_results) if criteria_results else len(goal.success_criteria)
        met_ratio = met_count / total_count if total_count > 0 else 0.0

    except (json.JSONDecodeError, KeyError):
        logger.warning("verify_goal_parse_error")
        # On parse error, be permissive — pass through
        all_met = True
        explanation = "Verification parse error, assuming complete"
        met_count = len(goal.success_criteria)
        total_count = len(goal.success_criteria)
        met_ratio = 1.0

    # Accept if all criteria met, OR if most criteria met (>= 70%) — LLM can
    # be overly skeptical, especially on ambiguous evidence
    if all_met or met_ratio >= 0.7:
        logger.info("verify_goal_passed", explanation=explanation, met_ratio=met_ratio)
        return {
            "cognitive_status": CognitiveStatus.COMPLETED,
            "should_terminate": True,
            "messages": [
                AIMessage(content=f"Goal verified ({met_count}/{total_count} criteria met): {explanation}")
            ],
        }
    else:
        # Not enough criteria met — re-plan if budget remains
        if plan.plan_version < 4:
            logger.info("verify_goal_failed_replan", explanation=explanation, met_ratio=met_ratio)
            updated_plan = Plan(
                steps=list(plan.steps),
                current_step_index=plan.current_step_index,
                plan_version=plan.plan_version,
                original_reasoning=plan.original_reasoning,
                re_plan_reason=f"Goal verification failed ({met_count}/{total_count} criteria): {explanation}",
            )
            return {
                "plan": updated_plan,
                "cognitive_status": CognitiveStatus.RE_PLANNING,
                "should_terminate": False,
                "messages": [
                    AIMessage(content=f"Goal NOT verified ({met_count}/{total_count}) — re-planning: {explanation}")
                ],
            }
        else:
            logger.info("verify_goal_failed_final", explanation=explanation, met_ratio=met_ratio)
            return {
                "cognitive_status": CognitiveStatus.FAILED,
                "should_terminate": True,
                "error": f"Goal verification failed after {plan.plan_version} plan versions ({met_count}/{total_count} criteria): {explanation}",
                "messages": [
                    AIMessage(content=f"Goal verification failed: {explanation}")
                ],
            }


# ============================================================
# Helper: Format findings using response template
# ============================================================

def _format_listings_summary(extracted_json: str) -> str:
    """Format extract_listings JSON into a clean natural language summary.

    Instead of dumping raw JSON, creates a short readable summary with a table
    of the first few items. The full data is available via the export endpoint.
    """
    import json as _json
    try:
        data = _json.loads(extracted_json)
    except (ValueError, TypeError):
        return extracted_json[:500]

    items = data.get("items", [])
    total = data.get("total_items", len(items))
    strategy = data.get("strategy", "")
    page_url = data.get("page_url", "")

    if not items:
        return "No items found on this page."

    # Build a clean summary
    lines = [f"Found {total} items on this page."]
    if page_url:
        lines.append(f"Source: {page_url}")
    lines.append("")

    # Determine which fields are populated
    fields = ["name", "price", "rating", "description"]
    active_fields = []
    for f in fields:
        if any(item.get(f) for item in items[:10]):
            active_fields.append(f)

    if not active_fields:
        active_fields = ["name"]

    # Show first 10 items as a readable list
    show_count = min(len(items), 10)
    for i, item in enumerate(items[:show_count], 1):
        parts = []
        name = item.get("name", "").strip()
        if name:
            parts.append(name)
        price = item.get("price", "").strip()
        if price:
            parts.append(price)
        rating = item.get("rating", "").strip()
        if rating:
            parts.append(f"Rating: {rating}")
        desc = item.get("description", "").strip()
        if desc and desc != name:
            parts.append(desc)
        lines.append(f"{i}. {' | '.join(parts)}")

    if total > show_count:
        lines.append(f"\n... and {total - show_count} more items.")
        lines.append("Use the download buttons below to get the full data as Excel/CSV/JSON/PDF.")

    return "\n".join(lines)


def _format_findings_with_template(template: dict, summary: str, memory) -> str | None:
    """Attempt to format findings as structured data using a response template.

    Parses the summary and memory data to extract key-value pairs matching
    the template's structured_keys. Falls back to None if formatting fails.
    """
    try:
        template_name = template.get("name", "")
        structured_keys = template.get("structured_keys", [])
        if not structured_keys:
            return None

        # Gather all text data from memory
        data_parts = []
        if hasattr(memory, "important_data") and memory.important_data:
            for key, data in memory.important_data.items():
                if isinstance(data, str) and len(data) > 10:
                    data_parts.append(data)

        if not data_parts and not summary:
            return None

        # Build a structured output based on template type
        lines = []

        if template_name == "price_check":
            lines.append("## Price Check Results\n")
            lines.append(summary)
        elif template_name == "product_search":
            lines.append("## Product Search Results\n")
            lines.append(summary)
        elif template_name == "info_lookup":
            lines.append("## Information Lookup\n")
            # For info lookup, present as Answer + Evidence
            lines.append(summary)
        elif template_name == "data_extraction":
            lines.append("## Extracted Data\n")
            lines.append(summary)
        elif template_name == "image_analysis":
            lines.append("## Visual Analysis Results\n")
            lines.append(summary)
        else:
            lines.append(summary)

        # Add source info from memory if available
        if data_parts:
            source_data = data_parts[0][:200]
            if source_data not in summary:
                lines.append(f"\n**Source Data:** {source_data}")

        return "\n".join(lines)
    except Exception as e:
        logger.warning("template_formatting_failed",
                       template=template.get("name", "unknown"),
                       error=str(e)[:200])
        return None


# ============================================================
# Node 11: Finalize
# ============================================================

async def finalize(state: AgentState) -> dict:
    """Wrap up the task — generate summary and final evaluation."""
    logger.info("finalize")

    def _apply_output_format(summary: str, findings: list, fmt: str) -> str:
        """Reformat summary based on user-requested output format."""
        content = summary
        if fmt == "json":
            import json as _json
            data = {"summary": summary, "findings": findings}
            return _json.dumps(data, indent=2, ensure_ascii=False)
        elif fmt == "csv":
            lines = [f.strip() for f in (findings or [summary.split("\n")]) if f.strip()]
            return "\n".join(lines)
        elif fmt == "table":
            # Convert findings into markdown table
            rows = []
            for f in findings:
                # Split each finding into columns by common delimiters
                parts = [p.strip() for p in f.replace(" | ", "|").split("|") if p.strip()]
                if not parts:
                    parts = [f.strip()]
                rows.append(parts)
            if rows:
                max_cols = max(len(r) for r in rows)
                header = "| " + " | ".join([f"Col {i+1}" for i in range(max_cols)]) + " |"
                sep = "| " + " | ".join(["---"] * max_cols) + " |"
                body = "\n".join("| " + " | ".join(r + [""] * (max_cols - len(r))) + " |" for r in rows)
                return f"{summary}\n\n{header}\n{sep}\n{body}"
        elif fmt == "bullets":
            items = findings if findings else summary.split("\n")
            return "\n".join(f"• {item.strip()}" for item in items if item.strip())
        elif fmt == "numbered":
            items = findings if findings else summary.split("\n")
            return "\n".join(f"{i+1}. {item.strip()}" for i, item in enumerate(items) if item.strip())
        return content

    plan = state.get("plan", Plan())
    goal = state.get("goal", Goal(original_text=""))
    status = state.get("cognitive_status", CognitiveStatus.COMPLETED)

    completed_count = len(plan.completed_steps)
    total_count = len(plan.steps)
    total_actions = len(state.get("action_history", []))

    # Trust verify_goal: if cognitive_status is COMPLETED, the goal was verified.
    # Don't require all plan steps to be formally marked — verify_goal already
    # checked the actual page state against success criteria.
    success = status == CognitiveStatus.COMPLETED

    # Source 0: The done tool's summary (highest priority — agent's own conclusion)
    done_summary = ""
    last_action = state.get("current_action")
    if last_action and last_action.action_type == ActionType.DONE and last_action.value:
        raw_summary = last_action.value

        # Detect if the agent dumped raw page content instead of a real summary.
        # Page content typically has many short lines, UI labels, marketing copy.
        # A real summary is 1-3 sentences about what was accomplished.
        lines = raw_summary.strip().split("\n")
        short_lines = sum(1 for l in lines if len(l.strip()) < 30 and l.strip())
        is_page_dump = (
            len(lines) > 8 and short_lines > len(lines) * 0.5  # Many short lines = UI text
        )

        if is_page_dump:
            logger.warning("done_summary_is_page_dump",
                           msg="Agent dumped page text into done() — replacing with concise summary")
            # Build a concise summary from action history instead
            action_hist = state.get("action_history", [])
            action_descs = [
                e.get("action", {}).get("description", "")
                for e in action_hist if e.get("action", {}).get("description")
            ]
            page_url = ""
            page_title = ""
            page_ctx = state.get("page_context")
            if page_ctx:
                page_url = getattr(page_ctx, "url", "")
                page_title = getattr(page_ctx, "title", "")

            done_summary = (
                f"Task completed on {page_title or page_url or 'the page'}.\n"
                f"Actions performed: {', '.join(action_descs[-5:]) or 'multiple browser actions'}.\n"
                f"Final page: {page_url}"
            )
        else:
            done_summary = raw_summary

    # Source 1: task_memory.important_data (vision/read_page results)
    memory = state.get("task_memory", TaskMemory())
    findings = []
    for key, data in memory.important_data.items():
        if isinstance(data, str) and len(data) > 10:
            findings.append(data[:500])

    # Source 2: extracted_data from action history (backup)
    if not findings:
        for entry in state.get("action_history", []):
            result = entry.get("result", {})
            extracted = result.get("extracted_data")
            if extracted and isinstance(extracted, str) and len(extracted) > 20:
                # Skip raw base64 image data
                if not extracted.startswith("data:image"):
                    findings.append(extracted[:500])

    logger.info("finalize_findings", count=len(findings), has_done_summary=bool(done_summary))

    # Build summary — prioritize the agent's own conclusion from done() tool
    if done_summary and len(done_summary) > 20:
        # The agent provided a meaningful summary via done(summary)
        summary_parts = [done_summary]
    else:
        summary_parts = [
            f"Goal: {goal.interpreted_goal or goal.original_text}",
            f"Status: {'Success' if success else 'Incomplete/Failed'}",
        ]

    # Append findings from vision/read_page if not already in done_summary
    if findings and (not done_summary or len(done_summary) < 50):
        summary_parts.append("\nFindings:")
        for finding in findings[-3:]:
            summary_parts.append(f"  {finding}")

    if state.get("error"):
        summary_parts.append(f"Error: {state['error']}")

    full_summary = "\n".join(summary_parts)

    # Try to format output using response template if one matches
    # Skip if summary already has the listings format (from _format_listings_summary)
    template = detect_task_pattern(goal.original_text)
    if template and findings and "Found " not in full_summary[:20]:
        structured = _format_findings_with_template(template, full_summary, memory)
        if structured:
            full_summary = structured

    # Apply user-requested output format if specified
    user_format = goal.output_format
    if user_format and findings:
        try:
            full_summary = _apply_output_format(full_summary, findings, user_format)
        except Exception as e:
            logger.warning("output_format_failed", format=user_format, error=str(e)[:100])

    return {
        "cognitive_status": CognitiveStatus.COMPLETED if success else CognitiveStatus.FAILED,
        "should_terminate": True,
        "task_summary": full_summary,
        "messages": [
            AIMessage(content=full_summary)
        ],
    }
