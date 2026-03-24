"""WebSocket message handler — bridges WebSocket messages to LangGraph agent.

This is the core orchestration layer:
1. Receives client messages (goal, user_response, action_result, dom_update, cancel)
2. Runs the LangGraph agent graph
3. Streams state updates back as WebSocket messages
4. Handles interrupts by pausing the graph and waiting for client response

The handler runs the graph in a background task and communicates
with the WebSocket via asyncio primitives.
"""

import asyncio
import time
import uuid
from typing import Any

import structlog
from fastapi import WebSocket, WebSocketDisconnect
from langchain_core.callbacks import AsyncCallbackHandler
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
from agent_core.schemas.messages import WSMessageType
from agent_core.server.session import Session, SessionManager
from agent_core.server.key_vault import key_vault

logger = structlog.get_logger("server.ws_handler")


class WebSocketStreamingHandler(AsyncCallbackHandler):
    """LangChain callback that streams LLM tokens to the WebSocket in real-time."""

    def __init__(self, ws: WebSocket, session_id: str):
        self.ws = ws
        self.session_id = session_id
        self._streaming = False
        self._buffer = ""
        self._token_count = 0
        # Throttle: send every N tokens or every M chars to avoid flooding
        self._send_interval = 3  # Send every 3 tokens
        self._current_node = ""

    async def on_llm_start(self, serialized: dict, prompts: Any, **kwargs) -> None:
        """Called when an LLM call starts."""
        self._streaming = True
        self._buffer = ""
        self._token_count = 0
        try:
            await self.ws.send_json({
                "type": "server_stream_start",
                "session_id": self.session_id,
                "timestamp": time.time(),
            })
        except Exception:
            pass

    async def on_llm_new_token(self, token: str, **kwargs) -> None:
        """Called for each new token from the LLM."""
        if not self._streaming:
            return

        self._buffer += token
        self._token_count += 1

        # Send buffered tokens every N tokens to avoid WS flooding
        if self._token_count % self._send_interval == 0 and self._buffer:
            try:
                await self.ws.send_json({
                    "type": "server_token",
                    "token": self._buffer,
                    "session_id": self.session_id,
                })
                self._buffer = ""
            except Exception:
                self._streaming = False

    async def on_llm_end(self, response: Any, **kwargs) -> None:
        """Called when an LLM call ends."""
        # Flush remaining buffer
        if self._buffer:
            try:
                await self.ws.send_json({
                    "type": "server_token",
                    "token": self._buffer,
                    "session_id": self.session_id,
                })
            except Exception:
                pass

        self._streaming = False
        self._buffer = ""

        try:
            await self.ws.send_json({
                "type": "server_stream_end",
                "session_id": self.session_id,
                "total_tokens": self._token_count,
                "timestamp": time.time(),
            })
        except Exception:
            pass

    async def on_llm_error(self, error: BaseException, **kwargs) -> None:
        """Called when an LLM call errors."""
        self._streaming = False
        self._buffer = ""


async def send_msg(ws: WebSocket, msg_type: str, **data) -> None:
    """Send a typed JSON message over WebSocket."""
    await ws.send_json({
        "type": msg_type,
        "timestamp": time.time(),
        **data,
    })


async def handle_websocket(
    ws: WebSocket,
    session_mgr: SessionManager,
    vault_token: str | None = None,
) -> None:
    """Main WebSocket handler — manages the full lifecycle of a connection.

    Flow:
    1. Accept connection, create session
    2. Wait for client_goal message
    3. Run agent graph, streaming updates to client
    4. Handle interrupts: pause graph, send interrupt to client, wait for response, resume
    5. On disconnect, clean up session
    """
    await ws.accept()
    session = session_mgr.create_session()
    session.vault_token = vault_token

    await send_msg(ws, "server_status",
                   cognitive_status="connected",
                   message=f"Connected. Session: {session.session_id}",
                   session_id=session.session_id)

    try:
        while True:
            # Wait for client message
            raw = await ws.receive_json()
            msg_type = raw.get("type", "")

            if msg_type == WSMessageType.CLIENT_GOAL.value:
                await _handle_goal(ws, session, raw)

            elif msg_type == WSMessageType.CLIENT_CANCEL.value:
                session.is_running = False
                await send_msg(ws, "server_status",
                               cognitive_status="idle",
                               message="Task cancelled",
                               session_id=session.session_id)

            elif msg_type == WSMessageType.CLIENT_DOM_UPDATE.value:
                # Update DOM snapshot mid-task (e.g., page changed)
                logger.info("dom_update_received", session_id=session.session_id)

            else:
                await send_msg(ws, "server_error",
                               message=f"Unexpected message type: {msg_type}",
                               recoverable=True)

    except WebSocketDisconnect:
        logger.info("websocket_disconnected", session_id=session.session_id)
    except Exception as e:
        logger.error("websocket_error", session_id=session.session_id, error=str(e))
        try:
            await send_msg(ws, "server_error",
                           message=f"Server error: {str(e)}",
                           recoverable=False)
        except Exception:
            pass
    finally:
        session.is_running = False
        session_mgr.remove_session(session.session_id)


async def _handle_goal(ws: WebSocket, session: Session, raw: dict) -> None:
    """Handle a client_goal message — run the full agent loop."""
    goal = raw.get("goal", "")
    dom_data = raw.get("dom_snapshot")
    model_override = raw.get("model_override")

    if not goal:
        await send_msg(ws, "server_error",
                       message="Goal is required",
                       recoverable=True)
        return

    # Parse DOM snapshot if provided
    page_context = None
    if dom_data:
        try:
            page_context = PageContext.model_validate(dom_data)
        except Exception as e:
            await send_msg(ws, "server_error",
                           message=f"Invalid DOM snapshot: {e}",
                           recoverable=True)
            return

    session.is_running = True
    session.current_goal = goal
    session.iteration_count = 0
    session.action_count = 0

    model_name = model_override or settings.ollama_model

    # Resolve API keys from vault (session keys > .env keys)
    api_keys = None
    if session.vault_token:
        vault_keys = key_vault.get_keys(session.vault_token)
        if vault_keys:
            api_keys = {}
            openai_key = vault_keys.openai_api_key.get_secret_value()
            groq_key = vault_keys.groq_api_key.get_secret_value()
            if openai_key:
                api_keys["openai_api_key"] = openai_key
            if groq_key:
                api_keys["groq_api_key"] = groq_key
            if vault_keys.ollama_base_url:
                api_keys["ollama_base_url"] = vault_keys.ollama_base_url

            # Use preferred model from vault if no explicit override
            if not model_override and vault_keys.preferred_model:
                model_name = vault_keys.preferred_model
            # If provider is set but no specific model, use that provider's default
            elif not model_override and vault_keys.preferred_provider:
                provider_defaults = {
                    "openai": settings.openai_model,
                    "groq": settings.groq_model,
                    "ollama": settings.ollama_model,
                }
                model_name = provider_defaults.get(
                    vault_keys.preferred_provider, model_name
                )

            if not api_keys:
                api_keys = None

    logger.info("goal_received",
                session_id=session.session_id,
                goal=goal,
                model=model_name,
                has_dom=page_context is not None,
                has_vault_keys=api_keys is not None)

    # Create initial state
    initial_state = create_initial_state(
        goal_text=goal,
        page_context=page_context,
        model_name=model_name,
        api_keys=api_keys,
    )
    # Create streaming callback to push LLM tokens to WebSocket in real-time
    streaming_handler = WebSocketStreamingHandler(ws, session.session_id)

    config = {
        "configurable": {"thread_id": session.thread_id},
        "run_name": f"agent_{session.session_id}",
        "callbacks": [streaming_handler],
        "metadata": {
            "session_id": session.session_id,
            "goal": goal[:100],
            "model": model_name,
        },
    }

    await send_msg(ws, "server_status",
                   cognitive_status="analyzing_goal",
                   message=f"Working on: {goal}",
                   session_id=session.session_id)

    # Run the agent graph with interrupt handling
    current_input = initial_state

    try:
        while session.is_running:
            # Stream graph execution with pre-node status updates
            async for event in session.graph.astream(current_input, config=config, stream_mode="updates"):
                if not session.is_running:
                    break

                for node_name, node_output in event.items():
                    if node_name == "__end__":
                        continue

                    if isinstance(node_output, dict):
                        await _stream_node_output(ws, session, node_name, node_output)

            if not session.is_running:
                break

            # Check for pending interrupts after stream ends
            state = await session.graph.aget_state(config)
            interrupt_data = _extract_interrupt(state)

            if interrupt_data:
                # Send interrupt to client and wait for response
                resume_value = await _handle_interrupt(ws, session, interrupt_data)
                if resume_value is None:
                    # Client disconnected or cancelled
                    break
                current_input = Command(resume=resume_value)
                continue

            # No interrupt — graph completed
            break

    except GraphInterrupt:
        # Explicit GraphInterrupt — handle same as stream-end interrupt
        state = await session.graph.aget_state(config)
        interrupt_data = _extract_interrupt(state)
        if interrupt_data:
            resume_value = await _handle_interrupt(ws, session, interrupt_data)
            if resume_value is not None:
                current_input = Command(resume=resume_value)
                # Continue the loop? No — we need to re-enter the while loop
                # This case is rare; the stream-end check handles most interrupts

    except WebSocketDisconnect:
        logger.info("client_disconnected_during_task", session_id=session.session_id)
        session.is_running = False
        return

    except Exception as e:
        logger.error("agent_execution_error",
                     session_id=session.session_id,
                     error=str(e))
        try:
            await send_msg(ws, "server_error",
                           message=f"Agent error: {str(e)}",
                           recoverable=False)
        except Exception:
            pass

    # Send final done message
    if session.is_running:
        try:
            final_state = await session.graph.aget_state(config)
            fv = final_state.values if final_state else {}
            await _send_done(ws, session, fv)
        except Exception:
            pass

    session.is_running = False


async def _stream_node_output(
    ws: WebSocket,
    session: Session,
    node_name: str,
    output: dict,
) -> None:
    """Convert a graph node's output into WebSocket messages for the client."""
    sid = session.session_id

    # Status updates with descriptive messages
    if "cognitive_status" in output:
        status = output["cognitive_status"]
        status_val = status.value if isinstance(status, CognitiveStatus) else str(status)

        # Build a human-readable status message
        status_msg = f"Node: {node_name}"
        if node_name == "analyze_and_plan":
            status_msg = "Understanding the task..."
        elif node_name == "decide_action":
            status_msg = "Deciding next action..."
        elif node_name == "execute_action_node":
            action = output.get("current_action")
            if action and hasattr(action, "description") and action.description:
                status_msg = f"Executing: {action.description[:80]}"
            else:
                status_msg = "Executing action..."
        elif node_name == "observe":
            status_msg = "Observing page changes..."
        elif node_name == "smart_evaluate":
            status_msg = "Checking result..."
        elif node_name == "evaluate":
            status_msg = "Evaluating outcome..."
        elif node_name == "self_critique_action":
            status_msg = "Planning next step..."
        elif node_name == "finalize":
            status_msg = "Completing task..."

        await send_msg(ws, "server_status",
                       cognitive_status=status_val,
                       message=status_msg,
                       iteration=output.get("iteration_count", session.iteration_count),
                       session_id=sid)

    # Track iterations
    if "iteration_count" in output:
        session.iteration_count = output["iteration_count"]

    # Goal analysis
    if "goal" in output and hasattr(output["goal"], "interpreted_goal"):
        g = output["goal"]
        if g.interpreted_goal:
            await send_msg(ws, "server_reasoning",
                           content=g.interpreted_goal,
                           reasoning_type="goal_analysis",
                           is_streaming=False,
                           is_final=True,
                           session_id=sid,
                           sub_goals=g.sub_goals,
                           complexity=g.complexity)

    # Plan — only send if it has more than 1 step (skip reactive mode's dummy plan)
    if "plan" in output and hasattr(output["plan"], "steps"):
        p = output["plan"]
        if p.steps and len(p.steps) > 1:
            steps_data = [
                {
                    "step_id": s.step_id,
                    "description": s.description,
                    "status": s.status.value,
                    "expected_outcome": s.expected_outcome,
                }
                for s in p.steps
            ]
            await send_msg(ws, "server_plan",
                           steps=steps_data,
                           plan_version=p.plan_version,
                           session_id=sid)

    # Internal thinking (Qwen <think> tags — model's internal reasoning process)
    if "current_thinking" in output and output["current_thinking"]:
        await send_msg(ws, "server_thinking",
                       content=str(output["current_thinking"])[:3000],
                       node=node_name,
                       session_id=sid)

    # Reasoning
    if "current_reasoning" in output and output["current_reasoning"]:
        await send_msg(ws, "server_reasoning",
                       content=str(output["current_reasoning"])[:2000],
                       reasoning_type="thinking",
                       is_streaming=False,
                       is_final=True,
                       session_id=sid)

    # Action decision (only from decide_action node — skip duplicates from confirm/execute)
    if "current_action" in output and output["current_action"] and node_name == "decide_action":
        a = output["current_action"]
        if hasattr(a, "action_type"):
            session.action_count += 1
            plan = output.get("plan") or getattr(session, "_last_plan", None)
            step_num = 0
            total_steps = 0
            if plan and hasattr(plan, "steps"):
                total_steps = len(plan.steps)
                step_num = plan.current_step_index + 1 if hasattr(plan, "current_step_index") else 0

            await send_msg(ws, "server_action_request",
                           action={
                               "action_id": a.action_id,
                               "action_type": a.action_type.value,
                               "element_id": a.element_id,
                               "value": a.value,
                               "description": a.description,
                               "reasoning": a.reasoning,
                               "confidence": a.confidence,
                               "risk_level": a.risk_level,
                           },
                           requires_confirmation=a.requires_confirmation,
                           step_number=step_num,
                           total_steps=total_steps,
                           session_id=sid)

    # Evaluation
    if "latest_evaluation" in output and output["latest_evaluation"]:
        e = output["latest_evaluation"]
        if hasattr(e, "action_succeeded"):
            await send_msg(ws, "server_evaluation",
                           action_succeeded=e.action_succeeded,
                           progress_percentage=e.progress_percentage,
                           summary=e.goal_progress,
                           next_step=e.next_action_suggestion or "",
                           session_id=sid)

    # Errors
    if "error" in output and output["error"]:
        await send_msg(ws, "server_error",
                       message=str(output["error"]),
                       recoverable=True,
                       session_id=sid)


def _extract_interrupt(state) -> dict | None:
    """Extract interrupt data from graph state, if any."""
    if not state or not state.tasks:
        return None
    for task in state.tasks:
        if hasattr(task, "interrupts") and task.interrupts:
            for intr in task.interrupts:
                return intr.value
    return None


async def _handle_interrupt(
    ws: WebSocket,
    session: Session,
    interrupt_data: dict,
) -> dict | None:
    """Send an interrupt to the client and wait for their response.

    Returns the resume value to pass back to the graph, or None if cancelled.
    """
    interrupt_id = str(uuid.uuid4())[:8]

    if "question" in interrupt_data:
        # Ask user for clarification
        await send_msg(ws, "server_interrupt",
                       interrupt_id=interrupt_id,
                       title="Input Required",
                       context=interrupt_data.get("context", ""),
                       fields=[{
                           "field_id": "answer",
                           "field_type": "text",
                           "label": interrupt_data.get("question", "Please provide input"),
                       }],
                       urgency="normal",
                       session_id=session.session_id)

    elif "action_id" in interrupt_data and "confidence" in interrupt_data:
        # Action confirmation — provide context about WHY confirmation is needed
        risk = interrupt_data.get("risk_level", "low")
        urgency = "warning" if risk in ("medium", "high") else "normal"

        # Descriptive title based on risk
        title = "Confirm Action"
        if risk == "high":
            desc = interrupt_data.get("description", "").lower()
            if any(w in desc for w in ("pay", "purchase", "checkout", "order")):
                title = "Payment Confirmation Required"
            elif any(w in desc for w in ("login", "sign in", "password")):
                title = "Login Credentials Required"
            elif any(w in desc for w in ("delete", "remove", "cancel")):
                title = "Destructive Action — Confirm"
            else:
                title = "High-Risk Action — Confirm"
        elif risk == "medium":
            title = "Confirm Cart Action"

        await send_msg(ws, "server_interrupt",
                       interrupt_id=interrupt_id,
                       title=title,
                       context=interrupt_data.get("description", ""),
                       fields=[{
                           "field_id": "confirmed",
                           "field_type": "confirm",
                           "label": f"{interrupt_data.get('action_type', 'action')} on element {interrupt_data.get('element_id', '?')}",
                           "description": interrupt_data.get("reasoning", ""),
                           "options": ["Yes, proceed", "No, skip"],
                       }],
                       urgency=urgency,
                       session_id=session.session_id)

    elif "action_id" in interrupt_data:
        # Action execution — send action to client for browser execution
        await send_msg(ws, "server_action_request",
                       action=interrupt_data,
                       requires_confirmation=False,
                       execute=True,
                       session_id=session.session_id)

    else:
        # Unknown interrupt type
        await send_msg(ws, "server_interrupt",
                       interrupt_id=interrupt_id,
                       title="Agent Needs Input",
                       context=str(interrupt_data),
                       fields=[{
                           "field_id": "response",
                           "field_type": "text",
                           "label": "Your response",
                       }],
                       urgency="normal",
                       session_id=session.session_id)

    # Wait for client response
    try:
        while True:
            raw = await asyncio.wait_for(ws.receive_json(), timeout=300)
            msg_type = raw.get("type", "")

            if msg_type == WSMessageType.CLIENT_USER_RESPONSE.value:
                # User responded to interrupt (confirmation or clarification)
                values = raw.get("values", {})
                if "confirmed" in values:
                    confirmed = values["confirmed"] in ("true", "True", True, "Yes, execute")
                    return {"confirmed": confirmed}
                elif "answer" in values:
                    return {"answer": values["answer"]}
                else:
                    return values

            elif msg_type == WSMessageType.CLIENT_ACTION_RESULT.value:
                # Browser executed action and sent result back
                result = raw.get("action_result", {})
                new_dom = raw.get("new_dom_snapshot")
                response = {
                    "status": result.get("status", "success"),
                    "message": result.get("message", ""),
                    "error": result.get("error"),
                    "extracted_data": result.get("extracted_data"),
                    "page_changed": result.get("page_changed", False),
                    "new_url": result.get("new_url"),
                    "execution_time_ms": result.get("execution_time_ms", 0),
                }

                # If this was a visual_check, process the screenshot with vision model
                extracted = result.get("extracted_data", "")
                if extracted and isinstance(extracted, str) and extracted.startswith("data:image") and len(extracted) > 1000:
                    try:
                        from agent_core.config import settings
                        import httpx
                        vision_model = settings.vision_model
                        if vision_model:
                            # Extract base64 from data URL
                            img_b64 = extracted.split(",", 1)[1] if "," in extracted else extracted
                            # Get the vision query from the action result message
                            action_msg = result.get("message", "")
                            vision_query = "Analyze this screenshot and describe what you see."
                            # The action description carries the task-specific query
                            action_desc = result.get("description", "")
                            if action_desc:
                                vision_query = action_desc
                            payload = {
                                "model": vision_model,
                                "messages": [{"role": "user", "content": f"Task: {vision_query}\n\nLook at this screenshot and answer the task. Be specific and factual about what you see.", "images": [img_b64]}],
                                "stream": False,
                            }
                            async with httpx.AsyncClient(timeout=120.0) as hc:
                                vr = await hc.post(f"{settings.ollama_base_url}/api/chat", json=payload)
                                vr.raise_for_status()
                                vdata = vr.json()
                            vision_text = vdata.get("message", {}).get("content", "")
                            response["extracted_data"] = vision_text[:3000] if vision_text else "Vision returned empty"
                            logger.info("vision_analysis_from_extension", result_length=len(response["extracted_data"]))
                    except Exception as ve:
                        logger.error("vision_analysis_error", error=str(ve))
                        # Don't silently fail — put the error in extracted_data so the agent knows
                        response["extracted_data"] = f"Vision analysis failed: {str(ve)[:300]}"

                if new_dom:
                    response["new_dom"] = new_dom
                return response

            elif msg_type == WSMessageType.CLIENT_CANCEL.value:
                session.is_running = False
                return None

            # Ignore other message types while waiting for interrupt response

    except asyncio.TimeoutError:
        logger.warning("interrupt_timeout", session_id=session.session_id)
        await send_msg(ws, "server_error",
                       message="Interrupt timed out (5 minutes). Task cancelled.",
                       recoverable=False,
                       session_id=session.session_id)
        session.is_running = False
        return None

    except WebSocketDisconnect:
        session.is_running = False
        return None


async def _send_done(ws: WebSocket, session: Session, final_values: dict) -> None:
    """Send the final done message summarizing the task."""
    status = final_values.get("cognitive_status", CognitiveStatus.FAILED)
    success = status == CognitiveStatus.COMPLETED if isinstance(status, CognitiveStatus) else False

    plan = final_values.get("plan")
    steps_total = len(plan.steps) if plan and hasattr(plan, "steps") else 0
    steps_completed = len(plan.completed_steps) if plan and hasattr(plan, "completed_steps") else 0

    summary = "Task completed successfully." if success else "Task could not be completed."

    # Pull actual findings from task_memory
    memory = final_values.get("task_memory")
    logger.info("finalize_memory_check",
                has_memory=memory is not None,
                memory_type=type(memory).__name__ if memory else "None",
                has_important_data=bool(getattr(memory, "important_data", None)) if memory else False)
    if memory:
        important_data = {}
        if hasattr(memory, "important_data"):
            important_data = memory.important_data
        elif isinstance(memory, dict):
            important_data = memory.get("important_data", {})

        if important_data:
            findings = []
            for key, data in important_data.items():
                if isinstance(data, str) and len(data) > 10:
                    findings.append(data[:500])
            if findings:
                summary = " | ".join(findings[-2:])  # Last 2 findings
                logger.info("finalize_findings_included", count=len(findings), total_chars=sum(len(f) for f in findings))

    # Override with explicit task_summary if set
    task_summary = final_values.get("task_summary", "")
    if task_summary:
        summary = task_summary

    # Auto-detect exportable data and store for download
    export_info = {}
    try:
        from agent_core.export.detector import detect_exportable_data
        from agent_core.export.store import export_store

        export_result = detect_exportable_data(final_values)
        if export_result:
            export_id = export_store.store(
                session_id=session.session_id,
                data=export_result["data"],
                metadata={
                    "goal": getattr(final_values.get("goal"), "original_text", ""),
                    "source": export_result["source"],
                },
            )
            export_info = {
                "export_available": True,
                "export_id": export_id,
                "export_formats": ["json", "csv", "xlsx", "pdf"],
                "export_items": len(export_result["data"]),
            }
            logger.info("export_data_stored",
                        export_id=export_id,
                        items=len(export_result["data"]),
                        source=export_result["source"])
    except Exception as e:
        logger.warning("export_detection_failed", error=str(e))

    await send_msg(ws, "server_done",
                   success=success,
                   summary=summary,
                   steps_completed=steps_completed,
                   steps_total=steps_total,
                   total_actions=session.action_count,
                   session_id=session.session_id,
                   **export_info)
