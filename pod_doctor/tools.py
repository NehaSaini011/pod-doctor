"""
The tool registry: what the LLM is told about each tool (name, description, JSON
parameters) and which Python function actually runs when it calls that tool.

Good descriptions matter a lot - the model decides WHEN to use a tool purely from
the description text below.
"""
from . import config, k8s_tools
from .rag import get_index


def search_runbooks(query, top_k=2):
    """RAG retrieval: semantic search over the SRE runbook knowledge base."""
    hits = get_index().search(query, k=max(1, min(int(top_k), 3)))
    return {"query": query, "backend": get_index().backend, "results": hits}


NS = {"type": "string", "description": f"Kubernetes namespace. Default '{config.DEFAULT_NAMESPACE}'."}

TOOLS = {
    "list_pods": {
        "fn": k8s_tools.list_pods,
        "description": "List every pod in a namespace with status (e.g. Running, CrashLoopBackOff, "
        "ImagePullBackOff, Pending), ready count, restarts and last termination reason/exit code. "
        "Always call this first to see what is unhealthy.",
        "parameters": {"type": "object", "properties": {"namespace": NS}},
    },
    "describe_pod": {
        "fn": k8s_tools.describe_pod,
        "description": "Run `kubectl describe pod`. Shows container state, Last State (reason + exit code), "
        "image, env sources, resource requests/limits, probes, and recent events. The best single tool "
        "for finding WHY a pod is unhealthy.",
        "parameters": {
            "type": "object",
            "properties": {"pod_name": {"type": "string", "description": "Exact pod name from list_pods."}, "namespace": NS},
            "required": ["pod_name"],
        },
    },
    "get_pod_logs": {
        "fn": k8s_tools.get_pod_logs,
        "description": "Read the container's application logs. For crash-looping pods set previous=true "
        "to read the logs of the attempt that crashed. Pods that never started have no logs.",
        "parameters": {
            "type": "object",
            "properties": {
                "pod_name": {"type": "string"},
                "namespace": NS,
                "previous": {"type": "boolean", "description": "Logs from the previous (crashed) container."},
                "tail_lines": {"type": "integer", "description": "How many lines from the end (max 200). Default 40."},
            },
            "required": ["pod_name"],
        },
    },
    "get_events": {
        "fn": k8s_tools.get_events,
        "description": "Recent Kubernetes events in a namespace, warnings first (FailedScheduling, "
        "Failed image pulls, probe failures, BackOff...). Optionally filter to one object name.",
        "parameters": {
            "type": "object",
            "properties": {"namespace": NS, "object_name": {"type": "string", "description": "Pod or other object name."}},
        },
    },
    "get_resource": {
        "fn": k8s_tools.get_resource,
        "description": "Read a resource's configuration, or list all of a kind if name is omitted. "
        "Kinds: deployment (to see the exact spec you would fix), service, configmap, secret "
        "(key names only), node (capacity/allocatable), endpoints (which pods receive Service traffic).",
        "parameters": {
            "type": "object",
            "properties": {
                "kind": {"type": "string", "enum": sorted(k8s_tools.ALLOWED_KINDS)},
                "name": {"type": "string", "description": "Resource name. Omit to list all."},
                "namespace": NS,
            },
            "required": ["kind"],
        },
    },
    "search_runbooks": {
        "fn": search_runbooks,
        "description": "Search the team's SRE runbook knowledge base (RAG). Use it once you know the "
        "failure type, e.g. 'OOMKilled exit code 137 memory limit', to get the documented "
        "confirmation steps and fix. Cite the runbook file name in your report.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Describe the symptom/failure in a few words."},
                "top_k": {"type": "integer", "description": "Number of runbooks to return (1-3). Default 2."},
            },
            "required": ["query"],
        },
    },
}


def call_tool(name, args):
    """Run a tool by name. Errors are returned to the model as data (so it can recover),
    never raised - an agent should be able to see and react to its own mistakes."""
    if name not in TOOLS:
        return {"error": f"Unknown tool '{name}'. Available: {list(TOOLS)}"}
    try:
        return TOOLS[name]["fn"](**(args or {}))
    except k8s_tools.ToolError as exc:
        return {"error": str(exc)}
    except TypeError as exc:  # model passed a wrong argument name
        return {"error": f"Bad arguments for {name}: {exc}"}
    except Exception as exc:
        return {"error": f"{type(exc).__name__}: {exc}"}
