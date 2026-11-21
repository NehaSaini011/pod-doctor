"""
The agent loop - the heart of the project.

This is a hand-written "ReAct" loop (Reason + Act) using Gemini's native function calling:

    1. Send the user's question + the list of tools to the LLM.
    2. The LLM either
         a) asks to call one or more tools  -> we run them and send the results back, go to 2
         b) answers in plain text           -> that is the final incident report, stop.
    3. A step limit stops the agent from looping forever.

The LLM decides which tools to call and in what order - we never hard-code the
investigation path. That is what makes it an *agent* rather than a script.

run_agent() is a generator: it yields an event for every step so the UI can show the
agent's reasoning live, and every run is saved to runs/ so it can be replayed later.
"""
import json
import re
import time
from datetime import datetime

from . import config
from .tools import TOOLS, call_tool

SYSTEM_PROMPT = f"""You are Pod Doctor, a senior Site Reliability Engineer who diagnoses broken
Kubernetes workloads. You can ONLY observe the cluster through your tools, which are read-only.

How to work:
1. Start with list_pods to see what is unhealthy (default namespace: "{config.DEFAULT_NAMESPACE}").
2. For each unhealthy pod relevant to the question, gather evidence: describe_pod (state, last state,
   exit code, events), get_pod_logs (use previous=true for restarting pods), get_events, and
   get_resource for the Deployment spec / ConfigMaps / node capacity when needed.
   Pods that are "Running" but not fully Ready ARE unhealthy - check their probes.
3. Once you know the failure type, call search_runbooks to retrieve the documented fix.
4. You may call several tools in the same turn when they are independent. Do not repeat a call
   whose result you already have.
5. Never guess. Every root cause must be backed by evidence you actually saw in tool output.
   If evidence is missing, say what you could not confirm.
6. You cannot change the cluster. Recommend fixes; never claim you applied them.

When you have enough evidence, reply with a final report in this Markdown format:

## Incident summary
One or two sentences, then a table: | Service | Symptom | Root cause | Severity (High/Medium/Low) |

## Findings
For each unhealthy service, a section "### <service> - <status>" containing:
- **Evidence:** the exact log line / event message / field values that prove the cause (quote them).
- **Root cause:** one or two plain-English sentences.
- **Fix:** the exact YAML change (a short snippet of the corrected field) or kubectl command.
- **Runbook:** the runbook file name(s) you retrieved, e.g. `oomkilled.md`.

## Healthy services
List them briefly.

## Next steps
Up to 3 prevention ideas.

If the user asks about one specific service, report only on that service.
"""


def _tool_declarations():
    from google.genai import types

    return [
        types.Tool(
            function_declarations=[
                types.FunctionDeclaration(name=name, description=t["description"], parameters_json_schema=t["parameters"])
                for name, t in TOOLS.items()
            ]
        )
    ]


def _generate_with_retry(client, contents, gen_config, attempts=4):
    """Free-tier APIs return 429/503 now and then - retry with exponential back-off."""
    from google.genai import errors

    for attempt in range(attempts):
        try:
            return client.models.generate_content(model=config.GEMINI_MODEL, contents=contents, config=gen_config)
        except errors.APIError as exc:
            if getattr(exc, "code", None) in (429, 500, 503, 504) and attempt < attempts - 1:
                time.sleep(2 ** (attempt + 1))
                continue
            raise


def run_agent(question, client=None, save=True):
    """Run the agent on one question. Yields event dicts:
    thought | tool_call | tool_result | final | error | stats."""
    from google import genai
    from google.genai import types

    started = time.time()
    events = []

    def emit(event):
        events.append(event)
        return event

    if client is None:
        if not config.GEMINI_API_KEY:
            yield emit({"type": "error", "text": "GEMINI_API_KEY is not set. Copy .env.example to .env and add your key."})
            return
        client = genai.Client(api_key=config.GEMINI_API_KEY)

    gen_config = types.GenerateContentConfig(
        system_instruction=SYSTEM_PROMPT,
        tools=_tool_declarations(),
        # We run the tools ourselves (that IS the agent loop), so turn off the SDK's auto mode.
        automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
        thinking_config=types.ThinkingConfig(include_thoughts=True),
        temperature=0.2,
    )
    contents = [types.Content(role="user", parts=[types.Part.from_text(text=question)])]
    tool_calls = 0

    try:
        for step in range(1, config.MAX_AGENT_STEPS + 1):
            response = _generate_with_retry(client, contents, gen_config)
            candidate = response.candidates[0] if response.candidates else None
            if candidate is None or candidate.content is None:
                yield emit({"type": "error", "text": "The model returned an empty response."})
                break

            parts = candidate.content.parts or []
            calls = [p.function_call for p in parts if p.function_call]
            for p in parts:  # the model's own reasoning summary, shown in the trace
                if p.text and (p.thought or calls):
                    yield emit({"type": "thought", "step": step, "text": p.text.strip()})

            if not calls:  # no tool requested -> this is the final answer
                final = "".join(p.text for p in parts if p.text and not p.thought).strip()
                yield emit({"type": "final", "text": final or "(The model returned no text.)"})
                break

            # Keep the model's turn in the history (this also preserves its thought signatures).
            contents.append(candidate.content)
            response_parts = []
            for fc in calls:
                args = dict(fc.args or {})
                yield emit({"type": "tool_call", "step": step, "name": fc.name, "args": args})
                t0 = time.time()
                result = call_tool(fc.name, args)
                tool_calls += 1
                yield emit({
                    "type": "tool_result", "step": step, "name": fc.name, "result": result,
                    "ok": "error" not in result, "ms": int((time.time() - t0) * 1000),
                })
                response_parts.append(
                    types.Part(function_response=types.FunctionResponse(id=fc.id, name=fc.name, response={"result": result}))
                )
            contents.append(types.Content(role="user", parts=response_parts))
        else:
            yield emit({"type": "error", "text": f"Stopped after {config.MAX_AGENT_STEPS} steps without a final answer."})
    except Exception as exc:
        yield emit({"type": "error", "text": f"{type(exc).__name__}: {exc}"})

    yield emit({"type": "stats", "model": config.GEMINI_MODEL, "tool_calls": tool_calls,
                "seconds": round(time.time() - started, 1)})
    if save:
        save_run(question, events)


# ------------------------------------------------------------------ saving & replay


def save_run(question, events):
    config.RUNS_DIR.mkdir(exist_ok=True)
    slug = re.sub(r"[^a-z0-9]+", "-", question.lower()).strip("-")[:40] or "run"
    path = config.RUNS_DIR / f"{datetime.now():%Y%m%d-%H%M%S}-{slug}.json"
    path.write_text(json.dumps({"question": question, "model": config.GEMINI_MODEL, "events": events}, indent=1, default=str),
                    encoding="utf-8")
    return path


def list_runs():
    return sorted(config.RUNS_DIR.glob("*.json"), reverse=True) if config.RUNS_DIR.exists() else []


def load_run(path):
    return json.loads(path.read_text(encoding="utf-8"))
