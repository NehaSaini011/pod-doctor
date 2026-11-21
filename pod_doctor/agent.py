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
   For any pod with restarts or CrashLoopBackOff, you MUST call get_pod_logs with previous=true
   before concluding - the crashed container's own logs are the strongest evidence.
   When you cite evidence, name the tool it really came from (e.g. "describe_pod shows...",
   "get_pod_logs (previous) shows..."). Never say "logs show" unless you called get_pod_logs.
6. You cannot change the cluster. Recommend fixes; never claim you applied them.

When you have enough evidence, reply with a final report in this Markdown format:

## Incident summary
One or two sentences, then a table: | Service | Symptom | Root cause | Severity (High/Medium/Low) |
The "Root cause" in the table must state the same cause as that service's Findings section, in short form.

## Findings
For each unhealthy service, a section "### <service> - <status>" containing:
- **Evidence:** the exact log line / event message / field values that prove the cause (quote them).
- **Root cause:** one or two plain-English sentences.
- **Fix:** the exact YAML change (a short snippet of the corrected field) or kubectl command.
- **Runbook:** the runbook file name(s) you retrieved, e.g. `oomkilled.md`. Only cite files that
  appeared in your search_runbooks results; if none fits, write "none retrieved".

## Healthy services
List them briefly.

## Next steps
Up to 3 prevention ideas.

If the user asks about one specific service, report only on that service.
"""


def check_citations(report, events):
    """Grounding check: every runbook the report cites must have been returned by search_runbooks
    during this run. Catches the model 'citing' a source it never actually read."""
    retrieved = {
        hit["runbook"]
        for ev in events
        if ev.get("type") == "tool_result" and ev.get("name") == "search_runbooks"
        for hit in (ev.get("result") or {}).get("results", [])
    }
    cited = set(re.findall(r"[a-z0-9][a-z0-9-]*\.md", report))
    return {
        "type": "citation_check",
        "cited": sorted(cited),
        "verified": sorted(cited & retrieved),
        "unverified": sorted(cited - retrieved),
        "retrieved": sorted(retrieved),
    }


def review_report(report, events):
    """Self-review (reflection): find gaps in a draft report that the agent must fix before
    the report is accepted. Returns a list of plain-English problems (empty = report OK)."""
    problems = []
    citations = check_citations(report, events)
    if citations["unverified"]:
        problems.append("These runbooks are cited but were never returned by search_runbooks: "
                        + ", ".join(citations["unverified"]) + ".")
    if "none retrieved" in report.lower():
        problems.append("Some findings say 'none retrieved'. Call search_runbooks for each of those "
                        "failure types and cite the runbook it returns.")
    # Restarting pods whose crashed-container logs were never read.
    latest_pods = next((ev["result"] for ev in reversed(events)
                        if ev.get("type") == "tool_result" and ev.get("name") == "list_pods"
                        and "pods" in (ev.get("result") or {})), None)
    if latest_pods:
        restarting = {p["pod"] for p in latest_pods["pods"] if p["restarts"] > 0 and not p["healthy"]}
        logs_read = {ev["args"].get("pod_name") for ev in events
                     if ev.get("type") == "tool_call" and ev.get("name") == "get_pod_logs"
                     and ev["args"].get("previous")}
        missing = sorted(restarting - logs_read)
        if missing:
            problems.append("You did not read the previous (crashed) container logs for: "
                            + ", ".join(missing) + ". Call get_pod_logs with previous=true for them.")
    return problems


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


MAX_REVISIONS = 1                  # how many times the self-review may send a draft back
RETRYABLE = {429, 500, 503, 504}   # busy / rate-limited / temporary server errors
SWITCHABLE = RETRYABLE | {404}     # also switch if a model name doesn't exist


def _generate_with_fallback(client, contents, gen_config, state, attempts_per_model=2):
    """Call Gemini. If a model is busy (503), rate-limited (429), times out, or doesn't exist (404),
    retry briefly and then fall back to the next model in config.MODELS.
    This is a generator: it yields 'notice' events so the UI can show what's happening, and stores
    the response in state["response"]. Once we switch model, we stay on the working one."""
    from google.genai import errors

    while state["model_idx"] < len(config.MODELS):
        model = config.MODELS[state["model_idx"]]
        for attempt in range(attempts_per_model):
            try:
                state["response"] = client.models.generate_content(model=model, contents=contents, config=gen_config)
                state["model"] = model
                return
            except errors.APIError as exc:
                code = getattr(exc, "code", None)
                if code not in SWITCHABLE:
                    raise
                reason = {404: "not available", 429: "rate-limited", 503: "busy", 504: "timing out"}.get(code, f"returning error {code}")
                if code == 404:
                    break  # retrying a missing model is pointless
            except Exception as exc:  # network timeouts surface with different classes per HTTP library
                if "timeout" not in type(exc).__name__.lower() and "timed out" not in str(exc).lower():
                    raise
                reason = "timing out"
            if attempt < attempts_per_model - 1:
                wait = 2 * (attempt + 1)
                yield {"type": "notice", "text": f"{model} is {reason}; retrying in {wait}s..."}
                time.sleep(wait)
        state["model_idx"] += 1
        if state["model_idx"] < len(config.MODELS):
            yield {"type": "notice", "text": f"{model} is {reason}; switching to backup model {config.MODELS[state['model_idx']]}."}
            if len(contents) > 1:
                # Gemini 3 history carries per-model "thought signatures" that another model may reject,
                # so the backup model restarts the investigation from the original question.
                del contents[1:]
                yield {"type": "notice", "text": "Restarting the investigation on the backup model."}
    raise RuntimeError(f"All models are unavailable right now ({', '.join(config.MODELS)}). Try again in a minute, "
                       "or use Replay in the sidebar.")


def run_agent(question, client=None, save=True):
    """Run the agent on one question. Yields event dicts:
    thought | notice | tool_call | tool_result | final | citation_check | error | stats."""
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
        # 90s per request: a hung request fails fast and falls back instead of blocking for minutes.
        client = genai.Client(api_key=config.GEMINI_API_KEY, http_options=types.HttpOptions(timeout=90_000))

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
    revisions = 0
    state = {"model_idx": 0, "model": config.MODELS[0], "response": None}

    try:
        for step in range(1, config.MAX_AGENT_STEPS + 1):
            for notice in _generate_with_fallback(client, contents, gen_config, state):
                yield emit(notice)
            response = state["response"]
            candidate = response.candidates[0] if response.candidates else None
            if candidate is None or candidate.content is None:
                yield emit({"type": "error", "text": "The model returned an empty response."})
                break

            parts = candidate.content.parts or []
            calls = [p.function_call for p in parts if p.function_call]
            for p in parts:  # the model's own reasoning summary, shown in the trace
                if p.text and (p.thought or calls):
                    yield emit({"type": "thought", "step": step, "text": p.text.strip()})

            if not calls:  # no tool requested -> this is a draft final answer
                final = "".join(p.text for p in parts if p.text and not p.thought).strip()
                problems = review_report(final, events) if final else []
                if problems and revisions < MAX_REVISIONS:
                    # Reflection: send the draft back with the gaps the code found.
                    revisions += 1
                    yield emit({"type": "notice", "text": "Self-review found gaps in the draft report: "
                                + " ".join(problems) + " Sending it back to the agent to fix."})
                    contents.append(candidate.content)
                    contents.append(types.Content(role="user", parts=[types.Part.from_text(
                        text="Self-review of your draft report found these problems:\n- " + "\n- ".join(problems)
                        + "\nFix them using your tools, then write the complete final report again in the same format.")]))
                    continue
                yield emit({"type": "final", "text": final or "(The model returned no text.)"})
                yield emit(check_citations(final, events))
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

    yield emit({"type": "stats", "model": state["model"], "tool_calls": tool_calls,
                "seconds": round(time.time() - started, 1)})
    if save:
        save_run(question, events, state["model"])


# ------------------------------------------------------------------ saving & replay


def save_run(question, events, model=None):
    config.RUNS_DIR.mkdir(exist_ok=True)
    slug = re.sub(r"[^a-z0-9]+", "-", question.lower()).strip("-")[:40] or "run"
    path = config.RUNS_DIR / f"{datetime.now():%Y%m%d-%H%M%S}-{slug}.json"
    path.write_text(json.dumps({"question": question, "model": model or config.GEMINI_MODEL, "events": events}, indent=1, default=str),
                    encoding="utf-8")
    return path


def list_runs():
    return sorted(config.RUNS_DIR.glob("*.json"), reverse=True) if config.RUNS_DIR.exists() else []


def load_run(path):
    return json.loads(path.read_text(encoding="utf-8"))
