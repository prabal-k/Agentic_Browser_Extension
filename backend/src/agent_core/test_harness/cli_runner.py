"""CLI Test Runner — Interactive testing of the cognitive agent.

Run the agent against DOM snapshots without a browser.
Handles LangGraph interrupts interactively in the terminal.

Usage:
    # Interactive mode — type a goal, provide DOM snapshot, interact with agent
    python -m agent_core.test_harness.cli_runner interactive

    # Batch mode — run predefined golden test scenarios
    python -m agent_core.test_harness.cli_runner batch

    # Single test — run one specific golden test
    python -m agent_core.test_harness.cli_runner test google_search

    # Check config — verify Ollama connection and settings
    python -m agent_core.test_harness.cli_runner check
"""

import asyncio
import json
import sys
import time
from pathlib import Path
from typing import Any

import click
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.prompt import Prompt, Confirm
from rich.syntax import Syntax
from rich.markdown import Markdown
from rich.live import Live
from rich.text import Text
from langgraph.types import Command
from langgraph.errors import GraphInterrupt

from agent_core.config import settings
from agent_core.schemas.agent import (
    AgentState,
    CognitiveStatus,
    create_initial_state,
)
from agent_core.schemas.dom import PageContext
from agent_core.schemas.actions import ActionStatus
from agent_core.agent.graph import create_agent_graph
from agent_core.logging import setup_logging

console = Console(force_terminal=True)

# Path to DOM snapshot fixtures
FIXTURES_DIR = Path(__file__).resolve().parent.parent.parent.parent / "tests" / "fixtures" / "dom_snapshots"
GOLDEN_TESTS_DIR = Path(__file__).resolve().parent / "golden_tests"


def _load_dom_snapshot(name_or_path: str) -> PageContext:
    """Load a DOM snapshot from fixture directory or absolute path."""
    # Try as fixture name first
    fixture_path = FIXTURES_DIR / f"{name_or_path}.json"
    if fixture_path.exists():
        with open(fixture_path) as f:
            return PageContext.model_validate(json.load(f))

    # Try as absolute/relative path
    path = Path(name_or_path)
    if path.exists():
        with open(path) as f:
            return PageContext.model_validate(json.load(f))

    raise FileNotFoundError(f"DOM snapshot not found: {name_or_path}")


def _list_available_snapshots() -> list[str]:
    """List available DOM snapshot fixtures."""
    if not FIXTURES_DIR.exists():
        return []
    return [f.stem for f in FIXTURES_DIR.glob("*.json")]


def _display_page_context(page_ctx: PageContext) -> None:
    """Display a page context summary in the terminal."""
    table = Table(title=f"Page: {page_ctx.title}")
    table.add_column("ID", style="cyan", width=5)
    table.add_column("Type", style="magenta", width=15)
    table.add_column("Text", style="white", width=40)
    table.add_column("State", style="green", width=15)
    table.add_column("Context", style="dim", width=25)

    for el in page_ctx.elements[:30]:  # Limit display
        state = []
        if el.is_visible:
            state.append("visible")
        if el.is_enabled:
            state.append("enabled")
        table.add_row(
            str(el.element_id),
            el.element_type.value,
            (el.text[:37] + "...") if len(el.text) > 40 else el.text,
            ", ".join(state),
            (el.parent_context[:22] + "...") if len(el.parent_context) > 25 else el.parent_context,
        )

    console.print(table)
    console.print(f"  URL: {page_ctx.url}")
    console.print(f"  Total elements: {len(page_ctx.elements)}")
    console.print(f"  Interactive: {len(page_ctx.interactive_elements)}")


def _display_state_update(key: str, value: Any) -> None:
    """Display a state update from a graph node."""
    if key == "cognitive_status" and isinstance(value, CognitiveStatus):
        status_colors = {
            CognitiveStatus.ANALYZING_GOAL: "blue",
            CognitiveStatus.CREATING_PLAN: "blue",
            CognitiveStatus.REASONING: "yellow",
            CognitiveStatus.DECIDING: "yellow",
            CognitiveStatus.AWAITING_CONFIRMATION: "magenta",
            CognitiveStatus.EXECUTING: "green",
            CognitiveStatus.OBSERVING: "cyan",
            CognitiveStatus.EVALUATING: "cyan",
            CognitiveStatus.SELF_CRITIQUING: "cyan",
            CognitiveStatus.RE_PLANNING: "red",
            CognitiveStatus.RETRYING: "red",
            CognitiveStatus.ASKING_USER: "magenta",
            CognitiveStatus.COMPLETED: "green bold",
            CognitiveStatus.FAILED: "red bold",
        }
        color = status_colors.get(value, "white")
        console.print(f"  [Status] [{color}]{value.value}[/{color}]")

    elif key == "goal" and hasattr(value, "interpreted_goal"):
        if value.interpreted_goal:
            console.print(Panel(
                f"[bold]Interpreted Goal:[/bold] {value.interpreted_goal}\n"
                f"[bold]Sub-goals:[/bold] {', '.join(value.sub_goals) if value.sub_goals else 'None'}\n"
                f"[bold]Complexity:[/bold] {value.complexity}\n"
                f"[bold]Achievable:[/bold] {value.is_achievable}",
                title="Goal Analysis",
                border_style="blue",
            ))

    elif key == "plan" and hasattr(value, "steps"):
        if value.steps:
            plan_text = f"Plan v{value.plan_version} ({len(value.steps)} steps):\n"
            for step in value.steps:
                icon = {"completed": "[x]", "in_progress": "[~]", "pending": "[ ]",
                        "failed": "[!]", "skipped": "[-]"}.get(step.status.value, "[?]")
                plan_text += f"  {icon} Step {step.step_id}: {step.description}\n"
            console.print(Panel(plan_text.strip(), title="Plan", border_style="blue"))

    elif key == "current_reasoning" and value:
        console.print(Panel(
            str(value)[:500],
            title="Reasoning (Chain of Thought)",
            border_style="yellow",
        ))

    elif key == "current_action" and value and hasattr(value, "action_type"):
        console.print(Panel(
            f"[bold]Action:[/bold] {value.action_type.value}\n"
            f"[bold]Element:[/bold] {value.element_id}\n"
            f"[bold]Value:[/bold] {value.value}\n"
            f"[bold]Description:[/bold] {value.description}\n"
            f"[bold]Confidence:[/bold] {value.confidence:.0%}\n"
            f"[bold]Risk:[/bold] {value.risk_level}",
            title="Planned Action",
            border_style="green",
        ))

    elif key == "latest_evaluation" and value and hasattr(value, "action_succeeded"):
        color = "green" if value.action_succeeded else "red"
        console.print(Panel(
            f"[bold]Succeeded:[/bold] [{color}]{value.action_succeeded}[/{color}]\n"
            f"[bold]Progress:[/bold] {value.progress_percentage:.0%}\n"
            f"[bold]Summary:[/bold] {value.goal_progress}\n"
            f"[bold]Re-plan:[/bold] {value.should_re_plan}",
            title="Evaluation",
            border_style=color,
        ))

    elif key == "error" and value:
        console.print(f"  [red bold]Error: {value}[/red bold]")


def _handle_interrupt(interrupt_value: Any) -> Any:
    """Handle a LangGraph interrupt by prompting the user in the terminal.

    The interrupt_value contains the data the agent sent when it paused.
    We render appropriate prompts and return the user's response.
    """
    console.print()

    if isinstance(interrupt_value, dict):
        # Determine interrupt type based on content
        # Order matters: check question first, then distinguish confirm vs execute
        # by presence of 'confidence' key (confirm_action includes it, execute does not)

        if "question" in interrupt_value:
            # Clarification question interrupt (ask_user_node)
            console.print(Panel(
                f"[bold]Question:[/bold] {interrupt_value.get('question', '')}\n"
                f"[bold]Context:[/bold] {interrupt_value.get('context', '')}",
                title="? Agent Needs Your Input",
                border_style="magenta",
            ))
            answer = Prompt.ask("[magenta]Your answer[/magenta]")
            return {"answer": answer}

        elif "action_id" in interrupt_value and "confidence" in interrupt_value:
            # Action confirmation interrupt (confirm_action node — has confidence/risk)
            console.print(Panel(
                f"[bold]Action:[/bold] {interrupt_value.get('action_type', 'unknown')}\n"
                f"[bold]Element:[/bold] {interrupt_value.get('element_id', 'N/A')}\n"
                f"[bold]Value:[/bold] {interrupt_value.get('value', 'N/A')}\n"
                f"[bold]Description:[/bold] {interrupt_value.get('description', '')}\n"
                f"[bold]Confidence:[/bold] {interrupt_value.get('confidence', 0):.0%}\n"
                f"[bold]Risk:[/bold] {interrupt_value.get('risk_level', 'unknown')}",
                title="Action Confirmation Required",
                border_style="magenta",
            ))
            confirmed = Confirm.ask("[magenta]Confirm this action?[/magenta]", default=True)
            return {"confirmed": confirmed}

        elif "action_id" in interrupt_value:
            # Action execution interrupt — simulate browser execution (execute_action_node)
            console.print(Panel(
                f"[bold]Execute:[/bold] {interrupt_value.get('action_type', 'unknown')}\n"
                f"[bold]Element:[/bold] {interrupt_value.get('element_id', 'N/A')}\n"
                f"[bold]Value:[/bold] {interrupt_value.get('value', 'N/A')}\n"
                f"[bold]Description:[/bold] {interrupt_value.get('description', '')}",
                title="🖥️  Browser Action (Simulated)",
                border_style="cyan",
            ))

            # In CLI mode, we simulate success/failure
            success = Confirm.ask(
                "[cyan]Simulate as successful?[/cyan]",
                default=True,
            )

            if success:
                return {
                    "status": "success",
                    "message": "Action executed successfully (simulated)",
                    "page_changed": True,
                    "execution_time_ms": 150.0,
                }
            else:
                error_msg = Prompt.ask(
                    "[red]Error message[/red]",
                    default="Element not found",
                )
                return {
                    "status": "element_not_found",
                    "message": "Action failed (simulated)",
                    "error": error_msg,
                    "page_changed": False,
                    "execution_time_ms": 50.0,
                }

    # Fallback for unknown interrupt types
    console.print(f"[yellow]Unknown interrupt:[/yellow] {interrupt_value}")
    answer = Prompt.ask("[yellow]Response[/yellow]", default="ok")
    return answer


async def run_interactive(
    goal: str,
    page_context: PageContext,
    model_name: str | None = None,
) -> dict:
    """Run the agent interactively, handling interrupts in the terminal.

    Returns a summary dict with results and metrics.
    """
    graph = create_agent_graph()

    initial_state = create_initial_state(
        goal_text=goal,
        page_context=page_context,
        model_name=model_name or settings.ollama_model,
    )

    config = {"configurable": {"thread_id": f"cli_{int(time.time())}"}}

    console.print(f"\n[bold]Starting agent with goal:[/bold] {goal}")
    console.print(f"[dim]Model: {model_name or settings.ollama_model}[/dim]")
    console.print(f"[dim]Ollama URL: {settings.ollama_base_url}[/dim]\n")

    start_time = time.time()
    total_iterations = 0

    # Run the graph, handling interrupts
    current_input = initial_state
    while True:
        try:
            # Stream the graph execution
            async for event in graph.astream(current_input, config=config):
                for node_name, node_output in event.items():
                    if node_name == "__end__":
                        continue

                    console.rule(f"[bold]{node_name}[/bold]", style="dim")

                    if isinstance(node_output, dict):
                        for key, value in node_output.items():
                            _display_state_update(key, value)

                        if node_output.get("iteration_count"):
                            total_iterations = node_output["iteration_count"]

            # Stream ended — check if this was an interrupt or completion
            state = await graph.aget_state(config)
            has_interrupt = False
            if state.tasks:
                for task in state.tasks:
                    if hasattr(task, "interrupts") and task.interrupts:
                        has_interrupt = True
                        for intr in task.interrupts:
                            user_response = _handle_interrupt(intr.value)
                            current_input = Command(resume=user_response)
                            break
                        break

            if has_interrupt:
                continue

            # No interrupt — graph completed
            break

        except GraphInterrupt:
            # Explicit GraphInterrupt exception
            state = await graph.aget_state(config)
            if state.tasks:
                for task in state.tasks:
                    if hasattr(task, "interrupts") and task.interrupts:
                        for intr in task.interrupts:
                            user_response = _handle_interrupt(intr.value)
                            current_input = Command(resume=user_response)
                            break
                continue
            else:
                console.print(f"[red]Interrupt but no tasks found[/red]")
                break

        except Exception as e:
            console.print(f"[red bold]Error: {e}[/red bold]")
            import traceback
            console.print(traceback.format_exc())
            break

    elapsed = time.time() - start_time

    # Get final state
    try:
        final_state = await graph.aget_state(config)
        final_values = final_state.values if final_state else {}
    except Exception:
        final_values = {}

    # Display summary
    console.print()
    console.rule("[bold]Task Complete[/bold]")

    summary = {
        "goal": goal,
        "elapsed_seconds": round(elapsed, 2),
        "iterations": total_iterations,
        "model": model_name or settings.ollama_model,
    }

    status = final_values.get("cognitive_status", CognitiveStatus.FAILED)
    if isinstance(status, CognitiveStatus):
        summary["final_status"] = status.value
    else:
        summary["final_status"] = str(status)

    plan = final_values.get("plan")
    if plan and hasattr(plan, "steps"):
        summary["plan_steps"] = len(plan.steps)
        summary["completed_steps"] = len(plan.completed_steps)
    summary["total_actions"] = len(final_values.get("action_history", []))

    table = Table(title="Results")
    table.add_column("Metric", style="cyan")
    table.add_column("Value", style="white")
    for k, v in summary.items():
        table.add_row(k, str(v))
    console.print(table)

    return summary


# ============================================================
# CLI Commands
# ============================================================

@click.group()
def cli():
    """Agentic Browser Extension — CLI Test Runner"""
    setup_logging()


@cli.command()
def check():
    """Check configuration and Ollama connectivity."""
    console.print(Panel(settings.display_config(), title="Configuration", border_style="blue"))

    # Check Ollama connectivity
    console.print("\n[bold]Checking Ollama connectivity...[/bold]")
    try:
        import httpx
        response = httpx.get(f"{settings.ollama_base_url}/api/tags", timeout=5)
        if response.status_code == 200:
            data = response.json()
            models = [m["name"] for m in data.get("models", [])]
            console.print(f"  [green]OK - Ollama is reachable at {settings.ollama_base_url}[/green]")
            console.print(f"  [green]OK - Available models: {', '.join(models[:10])}[/green]")

            # Check if configured model is available
            if settings.ollama_model in models or any(settings.ollama_model in m for m in models):
                console.print(f"  [green]OK - Model '{settings.ollama_model}' is available[/green]")
            else:
                console.print(f"  [yellow]WARN - Model '{settings.ollama_model}' not found. Available: {', '.join(models[:5])}[/yellow]")
                console.print(f"  [yellow]  Run: ollama pull {settings.ollama_model}[/yellow]")
        else:
            console.print(f"  [red]FAIL - Ollama returned status {response.status_code}[/red]")
    except Exception as e:
        console.print(f"  [red]FAIL - Cannot reach Ollama at {settings.ollama_base_url}[/red]")
        console.print(f"  [yellow]  Error: {type(e).__name__}[/yellow]")
        console.print(f"  [yellow]  Make sure Ollama is running and the URL in .env is correct[/yellow]")

    # Check OpenAI key
    api_key = settings.openai_api_key.get_secret_value()
    if api_key:
        console.print(f"  [green]OK - OpenAI API key is set[/green]")
    else:
        console.print(f"  [dim]-- OpenAI API key not set (optional)[/dim]")

    # List available snapshots
    snapshots = _list_available_snapshots()
    console.print(f"\n[bold]Available DOM snapshots:[/bold]")
    for s in snapshots:
        console.print(f"  - {s}")


@cli.command()
@click.option("--snapshot", "-s", default=None, help="DOM snapshot name or path")
@click.option("--model", "-m", default=None, help="Override LLM model name")
@click.option("--goal", "-g", default=None, help="Goal text (if not provided, will prompt)")
def interactive(snapshot: str | None, model: str | None, goal: str | None):
    """Run the agent interactively with a DOM snapshot."""

    # Select snapshot
    if not snapshot:
        available = _list_available_snapshots()
        console.print("[bold]Available DOM snapshots:[/bold]")
        for i, s in enumerate(available, 1):
            console.print(f"  {i}. {s}")
        choice = Prompt.ask(
            "Select snapshot (number or name)",
            default=available[0] if available else "google_search",
        )
        try:
            idx = int(choice) - 1
            snapshot = available[idx]
        except (ValueError, IndexError):
            snapshot = choice

    try:
        page_ctx = _load_dom_snapshot(snapshot)
    except FileNotFoundError as e:
        console.print(f"[red]{e}[/red]")
        return

    _display_page_context(page_ctx)

    # Get goal
    if not goal:
        goal = Prompt.ask("\n[bold]Enter your goal[/bold]")

    # Run
    asyncio.run(run_interactive(goal, page_ctx, model))


@cli.command()
@click.option("--model", "-m", default=None, help="Override LLM model name")
@click.option("--test", "-t", default=None, help="Run a specific golden test by name")
def batch(model: str | None, test: str | None):
    """Run golden test scenarios in batch mode."""

    golden_tests = _load_golden_tests()

    if test:
        golden_tests = [t for t in golden_tests if t["name"] == test]
        if not golden_tests:
            console.print(f"[red]Golden test '{test}' not found[/red]")
            return

    console.print(f"\n[bold]Running {len(golden_tests)} golden tests...[/bold]\n")

    results = []
    for i, gt in enumerate(golden_tests, 1):
        console.rule(f"Test {i}/{len(golden_tests)}: {gt['name']}")
        console.print(f"  Goal: {gt['goal']}")
        console.print(f"  Snapshot: {gt['snapshot']}")

        try:
            page_ctx = _load_dom_snapshot(gt["snapshot"])
        except FileNotFoundError as e:
            console.print(f"  [red]SKIP: {e}[/red]")
            results.append({"name": gt["name"], "status": "skipped", "error": str(e)})
            continue

        try:
            summary = asyncio.run(run_interactive(
                gt["goal"], page_ctx, model,
            ))
            results.append({"name": gt["name"], **summary})
        except Exception as e:
            console.print(f"  [red]FAILED: {e}[/red]")
            results.append({"name": gt["name"], "status": "error", "error": str(e)})

    # Summary table
    console.print()
    console.rule("[bold]Batch Results[/bold]")
    table = Table()
    table.add_column("Test", style="cyan")
    table.add_column("Status", style="white")
    table.add_column("Steps", style="white")
    table.add_column("Time (s)", style="white")

    for r in results:
        status = r.get("final_status", r.get("status", "unknown"))
        color = "green" if status == "completed" else "red"
        table.add_row(
            r["name"],
            f"[{color}]{status}[/{color}]",
            f"{r.get('completed_steps', '?')}/{r.get('plan_steps', '?')}",
            str(r.get("elapsed_seconds", "?")),
        )

    console.print(table)

    # Save results to file
    results_path = Path("test_results.json")
    with open(results_path, "w") as f:
        json.dump(results, f, indent=2, default=str)
    console.print(f"\n[dim]Results saved to {results_path}[/dim]")


def _load_golden_tests() -> list[dict]:
    """Load golden test definitions."""
    tests = []
    if GOLDEN_TESTS_DIR.exists():
        for f in sorted(GOLDEN_TESTS_DIR.glob("*.json")):
            with open(f) as fh:
                tests.append(json.load(fh))
    return tests


@cli.command()
def snapshots():
    """List and preview available DOM snapshots."""
    available = _list_available_snapshots()
    if not available:
        console.print("[yellow]No snapshots found[/yellow]")
        return

    for name in available:
        try:
            ctx = _load_dom_snapshot(name)
            console.print(f"\n[bold cyan]{name}[/bold cyan]")
            _display_page_context(ctx)
        except Exception as e:
            console.print(f"  [red]Error loading {name}: {e}[/red]")


if __name__ == "__main__":
    cli()
