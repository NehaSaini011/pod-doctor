"""
Pod Doctor - web UI.   Run with:   streamlit run app.py
"""
import hmac
import json
import time

import streamlit as st

from pod_doctor import config, k8s_tools
from pod_doctor.agent import list_runs, load_run, run_agent

st.set_page_config(page_title="Pod Doctor", page_icon="🩺", layout="wide")

STATUS_ICON = {"Running": "🟢", "Completed": "⚪"}
PRESETS = [
    "Diagnose every unhealthy pod in the shop namespace and tell me how to fix each one.",
    "Why is payment-api crashing?",
    "orders-api says Running but users get no response. Why?",
    "Why is search-api stuck in Pending?",
]
TOOL_ICON = {
    "list_pods": "📋", "describe_pod": "🔍", "get_pod_logs": "📜",
    "get_events": "⚡", "get_resource": "📄", "search_runbooks": "📚",
}


# ------------------------------------------------------------------ sidebar
with st.sidebar:
    st.header("⚙️ Setup")
    st.write(("✅" if config.GEMINI_API_KEY else "❌") + " Gemini API key")
    st.caption(f"Model: `{config.GEMINI_MODEL}`")
    SNAPSHOT = config.CLUSTER_MODE == "snapshot"
    if SNAPSHOT:
        try:
            st.caption(f"Cluster: 📸 snapshot taken {k8s_tools.load_snapshot()['captured_at'][:16].replace('T', ' ')} UTC")
        except k8s_tools.ToolError:
            st.caption("Cluster: 📸 snapshot (missing)")
    else:
        st.caption(f"Cluster context: `{config.KUBE_CONTEXT}` (live)")
    st.caption(f"Namespace: `{config.DEFAULT_NAMESPACE}`")
    st.caption(f"Runbooks in knowledge base: {len(list(config.KNOWLEDGE_BASE_DIR.glob('*.md')))}")
    st.divider()
    st.subheader("🎬 Replay a saved run")
    st.caption("Every investigation is saved. Replay one if the Wi-Fi fails during a demo.")
    runs = list_runs()
    choice = st.selectbox("Saved runs", runs, format_func=lambda p: p.stem, index=None, placeholder="Pick a run")
    replay_clicked = st.button("Replay", disabled=choice is None, width="stretch")
    if config.APP_PASSWORD:
        st.divider()
        code = st.text_input("🔑 Access code (for the live agent)", type="password")
        if code:
            st.session_state["unlocked"] = hmac.compare_digest(code, config.APP_PASSWORD)
            st.caption("✅ Unlocked" if st.session_state["unlocked"] else "❌ Wrong code")
    st.divider()
    st.caption("🔒 The agent is read-only: it can only run kubectl get / describe / logs.")


# ------------------------------------------------------------------ header + cluster health
st.title("🩺 Pod Doctor")
st.markdown("An **AI agent** that investigates broken Kubernetes workloads on its own, using "
            "read-only `kubectl` tools and an SRE runbook knowledge base (**RAG**), then writes an incident report.")
if SNAPSHOT:
    st.info("📸 **Snapshot mode.** The agent, the Gemini calls and the runbook search are all live: ask anything. "
            "The cluster data its tools read is a recording of the real demo cluster (a local `kind` cluster), "
            "captured with `scripts/snapshot.py`, because a public website cannot reach a laptop.")

st.subheader("Cluster health")
col_a, col_b = st.columns([1, 5])
if not SNAPSHOT:
    col_a.button("🔄 Refresh")
try:
    overview = k8s_tools.list_pods(config.DEFAULT_NAMESPACE)
    rows = [
        {
            "": STATUS_ICON.get(p["status"], "🔴") if p["healthy"] else ("🟡" if p["status"] == "Running" else "🔴"),
            "pod": p["pod"], "status": p["status"], "ready": p["ready"], "restarts": p["restarts"],
            "last termination": (f"{p['last_termination']['reason']} (exit {p['last_termination']['exit_code']})"
                                 if p["last_termination"] else ""),
            "age": p["age"],
        }
        for p in overview["pods"]
    ]
    col_b.caption(f"{overview['pod_count']} pods · {len(overview['unhealthy_pods'])} unhealthy")
    st.dataframe(rows, hide_index=True, width="stretch")
except k8s_tools.ToolError as exc:
    st.warning(f"Could not reach the cluster: {exc}\n\nStart it with `python scripts/cluster.py up`, "
               "or use **Replay** in the sidebar.")


# ------------------------------------------------------------------ rendering one event
def render(ev, area):
    t = ev["type"]
    if t == "thought":
        area.markdown(f"💭 *{ev['text'][:800]}*")
    elif t == "tool_call":
        args = ", ".join(f"{k}={json.dumps(v)}" for k, v in ev["args"].items())
        area.markdown(f"**Step {ev['step']}** · {TOOL_ICON.get(ev['name'], '🔧')} `{ev['name']}({args})`")
    elif t == "tool_result":
        label = ("✅ " if ev["ok"] else "⚠️ ") + f"result of {ev['name']} · {ev.get('ms', 0)} ms"
        with area.expander(label):
            res = ev["result"]
            if "describe" in res:
                st.code(res["describe"], language="text")
            elif "logs" in res:
                st.code(res["logs"] or res.get("note", ""), language="text")
            elif "results" in res:  # runbook search
                for hit in res["results"]:
                    st.markdown(f"**{hit['runbook']}** · similarity {hit['similarity']}")
                st.caption(f"retrieval backend: {res.get('backend')}")
            else:
                st.json(res)
    elif t == "notice":
        area.caption(f"⏳ {ev['text']}")
    elif t == "error":
        area.error(ev["text"])


def show_investigation(question, events_iter, delay=0.0):
    st.markdown(f"#### ❓ {question}")
    final, stats, citations = None, None, None
    trace = st.container(border=True)
    trace.markdown("**🤖 Agent trace** — what the agent decided to do, step by step")
    with st.spinner("Agent is investigating..."):
        for ev in events_iter:
            if ev["type"] == "final":
                final = ev["text"]
            elif ev["type"] == "stats":
                stats = ev
            elif ev["type"] == "citation_check":
                citations = ev
            else:
                render(ev, trace)
            if delay:
                time.sleep(delay)
    if stats:
        trace.caption(f"{stats['tool_calls']} tool calls · {stats['seconds']}s · model {stats['model']}")
    if final:
        st.success("Investigation complete — incident report below")
        with st.container(border=True):
            st.markdown(final)
        if citations:
            if citations["unverified"]:
                st.warning("🔎 **Citation check:** the report cites runbooks the agent never retrieved: "
                           + ", ".join(f"`{c}`" for c in citations["unverified"])
                           + ". Treat those citations as unverified.")
            elif citations["cited"]:
                st.caption(f"🔎 Citation check passed: all {len(citations['cited'])} cited runbooks "
                           f"({', '.join(citations['cited'])}) were actually retrieved by search_runbooks.")
            else:
                st.caption("🔎 Citation check: the report cites no runbooks.")


# ------------------------------------------------------------------ ask the agent
st.subheader("Ask the agent")
preset_cols = st.columns(len(PRESETS))
clicked = None
for col, text in zip(preset_cols, PRESETS):
    if col.button(text, width="stretch"):
        clicked = text
question = st.chat_input("Ask about your cluster, e.g. 'Why is report-worker restarting?'") or clicked

if replay_clicked and choice is not None:
    run = load_run(choice)
    st.info(f"Replaying saved run from `{choice.name}` (model: {run.get('model')})")
    show_investigation(run["question"], run["events"], delay=0.35)
elif question:
    st.session_state.setdefault("runs_used", 0)
    if config.APP_PASSWORD and not st.session_state.get("unlocked"):
        st.warning("Enter the access code in the sidebar to run the live agent. "
                   "You can still watch a saved run with **Replay**.")
    elif st.session_state["runs_used"] >= config.MAX_RUNS_PER_SESSION:
        st.warning(f"This session has used its {config.MAX_RUNS_PER_SESSION} live investigations. "
                   "Use **Replay** in the sidebar to watch saved runs.")
    else:
        st.session_state["runs_used"] += 1
        # On the public site, don't save visitors' questions where other visitors could replay them.
        show_investigation(question, run_agent(question, save=not SNAPSHOT))
