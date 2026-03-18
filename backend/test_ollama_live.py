"""Live Ollama integration test — runs agent against complex DOM snapshots.

This is a standalone script (not a pytest test) that validates the full
cognitive loop with a real LLM. It auto-handles interrupts without user input.

Usage:
    python test_ollama_live.py
"""

import asyncio
import json
import time
import traceback
from pathlib import Path

from agent_core.config import settings
from agent_core.agent.graph import create_agent_graph
from agent_core.schemas.agent import create_initial_state, CognitiveStatus
from agent_core.schemas.dom import PageContext
from langgraph.types import Command

FIXTURES_DIR = Path(__file__).parent / "tests" / "fixtures" / "dom_snapshots"

# Test scenarios with complex goals on real website DOMs
TEST_SCENARIOS = [
    {
        "name": "github_explore_search_repo",
        "snapshot": "github_explore",
        "goal": "Find and click on the trending repository about AI agents, then navigate to its Issues tab",
        "max_iterations": 15,
    },
    {
        "name": "imdb_find_top_movie",
        "snapshot": "imdb_top",
        "goal": "Find 'The Shawshank Redemption' in the top movies list and click on it to see its details",
        "max_iterations": 10,
    },
    {
        "name": "github_sign_in_flow",
        "snapshot": "github_explore",
        "goal": "I want to sign in to GitHub and then search for 'langchain' repositories",
        "max_iterations": 15,
    },
]

# Set to True to only run the first scenario for quick validation
QUICK_MODE = False


def auto_handle_interrupt(interrupt_value) -> dict:
    """Auto-respond to interrupts for non-interactive testing.

    Distinguish interrupt types:
    - confirm_action: has action_id + confidence + risk_level
    - execute_action_node: has action_id but NO confidence
    - ask_user_node: has question
    """
    if isinstance(interrupt_value, dict):
        if "question" in interrupt_value:
            # Clarification question interrupt
            q = interrupt_value.get("question", "")
            print(f"    [AUTO-ANSWER] Q: {q[:100]}")
            return {"answer": "Yes, proceed with the default option"}

        elif "action_id" in interrupt_value and "confidence" in interrupt_value:
            # Confirmation interrupt (has confidence/risk_level from confirm_action)
            print(f"    [AUTO-CONFIRM] {interrupt_value.get('action_type')}"
                  f" on element {interrupt_value.get('element_id')}"
                  f" conf={interrupt_value.get('confidence', 0):.0%}")
            return {"confirmed": True}

        elif "action_id" in interrupt_value:
            # Execution interrupt (no confidence — from execute_action_node)
            print(f"    [AUTO-EXECUTE] {interrupt_value.get('action_type')}"
                  f" on element {interrupt_value.get('element_id')}"
                  f" - {interrupt_value.get('description', '')[:80]}")
            return {
                "status": "success",
                "message": "Action executed successfully (simulated)",
                "page_changed": True,
                "execution_time_ms": 120.0,
            }

        elif "action_type" in interrupt_value:
            # Fallback for action-related interrupts
            print(f"    [AUTO-CONFIRM] {interrupt_value.get('action_type')}"
                  f" on element {interrupt_value.get('element_id')}")
            return {"confirmed": True}

    print(f"    [AUTO-FALLBACK] Unknown interrupt: {type(interrupt_value)}")
    return {"answer": "ok"}


async def run_scenario(scenario: dict) -> dict:
    """Run a single test scenario and return results."""
    name = scenario["name"]
    snapshot_name = scenario["snapshot"]
    goal = scenario["goal"]

    print(f"\n{'='*70}")
    print(f"SCENARIO: {name}")
    print(f"GOAL: {goal}")
    print(f"SNAPSHOT: {snapshot_name}")
    print(f"MODEL: {settings.ollama_model}")
    print(f"{'='*70}")

    # Load DOM
    snapshot_path = FIXTURES_DIR / f"{snapshot_name}.json"
    if not snapshot_path.exists():
        return {"name": name, "status": "skipped", "error": f"Snapshot not found: {snapshot_name}"}

    with open(snapshot_path) as f:
        page_ctx = PageContext.model_validate(json.load(f))

    print(f"  Page: {page_ctx.title} ({len(page_ctx.elements)} elements, "
          f"{len(page_ctx.interactive_elements)} interactive)")

    # Create graph and initial state
    graph = create_agent_graph()
    initial_state = create_initial_state(
        goal_text=goal,
        page_context=page_ctx,
        model_name=settings.ollama_model,
    )
    config = {"configurable": {"thread_id": f"test_{name}_{int(time.time())}"}}

    start_time = time.time()
    iterations = 0
    nodes_visited = []
    interrupt_count = 0

    current_input = initial_state

    from langgraph.errors import GraphInterrupt

    try:
        while True:
            try:
                async for event in graph.astream(current_input, config=config):
                    for node_name, node_output in event.items():
                        if node_name == "__end__":
                            continue

                        nodes_visited.append(node_name)
                        print(f"\n  >> Node: {node_name}")

                        if isinstance(node_output, dict):
                            # Show key state changes
                            if "cognitive_status" in node_output:
                                status = node_output["cognitive_status"]
                                if isinstance(status, CognitiveStatus):
                                    print(f"     Status: {status.value}")

                            if "goal" in node_output and hasattr(node_output["goal"], "interpreted_goal"):
                                g = node_output["goal"]
                                if g.interpreted_goal:
                                    print(f"     Goal: {g.interpreted_goal}")
                                    print(f"     Sub-goals: {g.sub_goals}")
                                    print(f"     Complexity: {g.complexity}")

                            if "plan" in node_output and hasattr(node_output["plan"], "steps"):
                                p = node_output["plan"]
                                if p.steps:
                                    print(f"     Plan v{p.plan_version} ({len(p.steps)} steps):")
                                    for step in p.steps:
                                        print(f"       - {step.description}")

                            if "current_reasoning" in node_output and node_output["current_reasoning"]:
                                r = node_output["current_reasoning"]
                                text = str(r)[:300]
                                print(f"     Reasoning: {text}")

                            if "current_action" in node_output and node_output["current_action"]:
                                a = node_output["current_action"]
                                if hasattr(a, "action_type"):
                                    print(f"     Action: {a.action_type.value} "
                                          f"elem={a.element_id} "
                                          f"val={a.value} "
                                          f"conf={a.confidence:.0%}")

                            if "latest_evaluation" in node_output and node_output["latest_evaluation"]:
                                e = node_output["latest_evaluation"]
                                if hasattr(e, "action_succeeded"):
                                    print(f"     Eval: succeeded={e.action_succeeded} "
                                          f"progress={e.progress_percentage:.0%}")

                            if "error" in node_output and node_output["error"]:
                                print(f"     ERROR: {node_output['error']}")

                            if "iteration_count" in node_output:
                                iterations = node_output["iteration_count"]

                # Stream ended — check if it was an interrupt or completion
                state = await graph.aget_state(config)

                # Check for pending interrupts
                has_interrupt = False
                if state.tasks:
                    for task in state.tasks:
                        if hasattr(task, "interrupts") and task.interrupts:
                            has_interrupt = True
                            interrupt_count += 1
                            for intr in task.interrupts:
                                response = auto_handle_interrupt(intr.value)
                                current_input = Command(resume=response)
                                break
                            break

                if has_interrupt:
                    continue

                # No interrupt — graph completed
                break

            except GraphInterrupt:
                # Explicit GraphInterrupt exception
                interrupt_count += 1
                state = await graph.aget_state(config)
                if state.tasks:
                    for task in state.tasks:
                        if hasattr(task, "interrupts") and task.interrupts:
                            for intr in task.interrupts:
                                response = auto_handle_interrupt(intr.value)
                                current_input = Command(resume=response)
                                break
                    continue
                else:
                    print("  [!] GraphInterrupt raised but no tasks found")
                    break

    except Exception as e:
        elapsed = time.time() - start_time
        print(f"\n  [FAILED] {type(e).__name__}: {e}")
        traceback.print_exc()
        return {
            "name": name,
            "status": "error",
            "error": str(e),
            "elapsed_seconds": round(elapsed, 2),
            "nodes_visited": nodes_visited,
        }

    elapsed = time.time() - start_time

    # Get final state
    try:
        final_state = await graph.aget_state(config)
        fv = final_state.values if final_state else {}
    except Exception:
        fv = {}

    final_status = fv.get("cognitive_status", CognitiveStatus.FAILED)
    if isinstance(final_status, CognitiveStatus):
        final_status = final_status.value

    plan = fv.get("plan")
    plan_steps = len(plan.steps) if plan and hasattr(plan, "steps") else 0
    completed_steps = len(plan.completed_steps) if plan and hasattr(plan, "completed_steps") else 0
    total_actions = len(fv.get("action_history", []))

    result = {
        "name": name,
        "status": final_status,
        "elapsed_seconds": round(elapsed, 2),
        "iterations": iterations,
        "nodes_visited_count": len(nodes_visited),
        "unique_nodes": list(set(nodes_visited)),
        "interrupt_count": interrupt_count,
        "plan_steps": plan_steps,
        "completed_steps": completed_steps,
        "total_actions": total_actions,
    }

    print(f"\n  --- Result ---")
    print(f"  Status: {final_status}")
    print(f"  Time: {elapsed:.1f}s")
    print(f"  Iterations: {iterations}")
    print(f"  Nodes visited: {len(nodes_visited)} ({len(set(nodes_visited))} unique)")
    print(f"  Interrupts handled: {interrupt_count}")
    print(f"  Plan: {completed_steps}/{plan_steps} steps completed")
    print(f"  Actions taken: {total_actions}")

    return result


async def main():
    print("=" * 70)
    print("LIVE OLLAMA INTEGRATION TEST")
    print(f"Server: {settings.ollama_base_url}")
    print(f"Model: {settings.ollama_model}")
    print("=" * 70)

    # Verify connectivity first
    import httpx
    try:
        resp = httpx.get(f"{settings.ollama_base_url}/api/tags", timeout=5)
        models = [m["name"] for m in resp.json().get("models", [])]
        print(f"OK - Ollama reachable. Models: {', '.join(models)}")
    except Exception as e:
        print(f"FATAL - Cannot reach Ollama: {e}")
        return

    if not any(settings.ollama_model in m for m in models):
        print(f"FATAL - Model '{settings.ollama_model}' not available")
        return

    # Run scenarios
    scenarios = TEST_SCENARIOS[:1] if QUICK_MODE else TEST_SCENARIOS
    all_results = []
    for scenario in scenarios:
        result = await run_scenario(scenario)
        all_results.append(result)

    # Final summary
    print("\n" + "=" * 70)
    print("SUMMARY")
    print("=" * 70)
    print(f"{'Test':<35} {'Status':<15} {'Time':<10} {'Actions':<10}")
    print("-" * 70)
    for r in all_results:
        print(f"{r['name']:<35} {r.get('status','?'):<15} "
              f"{r.get('elapsed_seconds','?'):<10} {r.get('total_actions','?'):<10}")

    # Save results
    results_path = Path("live_test_results.json")
    with open(results_path, "w") as f:
        json.dump(all_results, f, indent=2, default=str)
    print(f"\nResults saved to {results_path}")


if __name__ == "__main__":
    asyncio.run(main())
