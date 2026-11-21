"""
Pod Doctor - web UI.   Run with:   streamlit run app.py
"""
import hmac
import json
import time

import streamlit as st

from pod_doctor import chaos, config, k8s_tools
from pod_doctor.agent import list_runs, load_run, run_agent
from pod_doctor.samples import EXAMPLE_DESCRIBE

st.set_page_config(page_title="Pod Doctor", page_icon="🩺", layout="wide")

SNAPSHOT = config.CLUSTER_MODE == "snapshot"
STATUS_ICON = {"Running": "🟢", "Completed": "⚪"}
TOOL_ICON = {
    "list_pods": "📋", "describe_pod": "🔍", "get_pod_logs": "📜",
    "get_events": "⚡", "get_resource": "📄", "search_runbooks": "📚",
}
MAX_PASTE_CHARS = 20_000


# ------------------------------------------------------------------ sidebar
with st.sidebar:
    st.header("⚙️ Setup")
    st.write(("✅" if config.GEMINI_API_KEY else "❌") + " Gemini API key")
    st.caption(f"Model: `{config.GEMINI_MODEL}` (backups: {', '.join(config.FALLBACK_MODELS) or 'none'})")

    st.subheader("☸️ Cluster")
    if SNAPSHOT:
        try:
            taken = k8s_tools.load_snapshot()["captured_at"][:16].replace("T", " ")
            st.caption(f"📸 Snapshot of the demo cluster, taken {taken} UTC")
        except k8s_tools.ToolError:
            st.caption("📸 Snapshot (missing - run scripts/snapshot.py)")
        st.caption(f"Namespace: `{config.DEFAULT_NAMESPACE}`")
    else:
        # Picker: any cluster and namespace your kubectl can see. The agent stays read-only everywhere.
        try:
            contexts = k8s_tools.list_contexts()
        except k8s_tools.ToolError:
            contexts = []
        if config.KUBE_CONTEXT not in contexts:
            contexts.insert(0, config.KUBE_CONTEXT)
        st.session_state.setdefault("context", config.KUBE_CONTEXT)
        config.KUBE_CONTEXT = st.selectbox("Cluster (kube context)", contexts, key="context")
        try:
            namespaces = k8s_tools.list_namespaces()
        except k8s_tools.ToolError:
            namespaces = []
        st.session_state.setdefault("namespace", config.DEFAULT_NAMESPACE)
        if st.session_state["namespace"] not in namespaces:
            namespaces.insert(0, st.session_state["namespace"])
        config.DEFAULT_NAMESPACE = st.selectbox("Namespace", namespaces, key="namespace")
        st.caption("Pick any cluster or namespace your kubectl can see. The agent can only read.")
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


# ------------------------------------------------------------------ helpers
def render(ev, area):
    t = ev["type"]
    if t == "thought":
        area.markdown(f"💭 *{ev['text'][:800]}*")
    elif t == "notice":
        area.caption(f"⏳ {ev['text']}")
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
    elif t == "error":
        area.error(ev["text"])


def show_investigation(title, events_iter, delay=0.0):
    st.markdown(f"#### ❓ {title}")
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


def allowed_to_run():
    """Access code + per-session limit, so a public URL can't burn through the API key."""
    st.session_state.setdefault("runs_used", 0)
    if config.APP_PASSWORD and not st.session_state.get("unlocked"):
        st.warning("Enter the access code in the sidebar to run the live agent. "
                   "You can still watch a saved run with **Replay**.")
        return False
    if st.session_state["runs_used"] >= config.MAX_RUNS_PER_SESSION:
        st.warning(f"This session has used its {config.MAX_RUNS_PER_SESSION} live investigations. "
                   "Use **Replay** in the sidebar to watch saved runs.")
        return False
    st.session_state["runs_used"] += 1
    return True


def load_example():
    st.session_state["paste_text"] = EXAMPLE_DESCRIBE


# ------------------------------------------------------------------ header
st.title("🩺 Pod Doctor")
st.markdown("An **AI agent** that investigates broken Kubernetes workloads on its own, using "
            "read-only `kubectl` tools and an SRE runbook knowledge base (**RAG**), then writes an incident report.")
if SNAPSHOT:
    st.info("📸 **Snapshot mode.** The agent, the Gemini calls and the runbook search are all live: ask anything. "
            "The cluster data its tools read is a recording of the real demo cluster (a local `kind` cluster), "
            "captured with `scripts/snapshot.py`, because a public website cannot reach a laptop. "
            "To analyze **your own** cluster, use the second tab.")

tab_cluster, tab_paste = st.tabs(["🩺 Diagnose the cluster", "📋 Analyze your own kubectl output"])

# ------------------------------------------------------------------ tab 1: the cluster
with tab_cluster:
    st.subheader(f"Cluster health · `{config.DEFAULT_NAMESPACE}`")
    col_a, col_b = st.columns([1, 5])
    if not SNAPSHOT:
        col_a.button("🔄 Refresh")
    try:
        overview = k8s_tools.list_pods()
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

    # ---- Break it yourself (live demo cluster only)
    broken = {}
    if chaos.available():
        with st.expander("🧪 **Break it yourself** — inject a fault, then let the agent find it blind"):
            st.caption("Pick a Deployment and a fault. The panel changes the Deployment; the agent is never told "
                       "what changed. Kubernetes keeps the old healthy pods while the new broken one fails, "
                       "exactly like a stuck bad rollout in production. (Run `python scripts/cluster.py fix all` "
                       "first if you want every service healthy before breaking one.)")
            try:
                deployments = chaos.list_deployments()
                c1, c2, c3 = st.columns([2, 3, 1])
                target = c1.selectbox("Deployment", deployments,
                                      index=deployments.index("web-frontend") if "web-frontend" in deployments else 0)
                fault = c2.selectbox("Fault", list(chaos.FAULTS), format_func=lambda k: chaos.FAULTS[k][0])
                c3.write("")
                if c3.button("💥 Inject", width="stretch"):
                    chaos.inject(target, fault)
                    st.success(f"Fault injected into **{target}**. Wait ~30 seconds, click Refresh, then ask the agent "
                               "below. It doesn't know what you changed.")
                broken = chaos.injected()
                if broken:
                    st.markdown("**Currently broken by this panel:**")
                    for dep, labels in broken.items():
                        b1, b2 = st.columns([5, 1])
                        with b1.popover(f"`{dep}` — reveal the answer"):
                            st.write("Injected: " + "; ".join(labels))
                        if b2.button("↩️ Undo", key=f"undo-{dep}", width="stretch"):
                            chaos.restore(dep)
                            st.rerun()
            except k8s_tools.ToolError as exc:
                st.warning(f"Fault injection unavailable: {exc}")

    # ---- Ask the agent
    st.subheader("Ask the agent")
    ns = config.DEFAULT_NAMESPACE
    presets = [f"Diagnose every unhealthy pod in the {ns} namespace and tell me how to fix each one."]
    presets += [f"Something is wrong with {dep}. Find out what and how to fix it." for dep in broken]
    if ns == "shop" and not broken:
        presets += ["Why is payment-api crashing?", "orders-api says Running but users get no response. Why?",
                    "Why is search-api stuck in Pending?"]
    clicked = None
    for col, text in zip(st.columns(len(presets)), presets):
        if col.button(text, width="stretch"):
            clicked = text

# ------------------------------------------------------------------ tab 2: paste your own output
with tab_paste:
    st.subheader("Analyze output from your own cluster")
    st.markdown("Paste the output of `kubectl describe pod <name>`, `kubectl logs <name> --previous`, "
                "`kubectl get events` or `kubectl get pods` from **any** cluster. The agent analyzes it with "
                "the same runbook knowledge base (RAG). No cluster access or credentials needed.")
    st.caption("⚠️ The text is sent to the Gemini API. Remove passwords, tokens and other secrets before pasting.")
    st.button("Load an example (fictional pod)", on_click=load_example)
    pasted = st.text_area("kubectl output", key="paste_text", height=260,
                          placeholder="Name:  my-app-5f6b...\nNamespace:  default\n...")
    note = st.text_input("Anything else the agent should know? (optional)",
                         placeholder="e.g. this started after today's deploy")
    analyze = st.button("🔍 Analyze", type="primary", disabled=not pasted.strip())

# ------------------------------------------------------------------ run
question = st.chat_input("Ask about the cluster, e.g. 'Why is report-worker restarting?'") or clicked

if replay_clicked and choice is not None:
    run = load_run(choice)
    target_tab = tab_paste if run.get("mode") == "paste" else tab_cluster
    with target_tab:
        st.info(f"Replaying saved run `{choice.name}` (model: {run.get('model')})")
        title = "Analyze pasted kubectl output" if run.get("mode") == "paste" else run["question"]
        show_investigation(title, run["events"], delay=0.35)
elif question:
    with tab_cluster:
        if allowed_to_run():
            # On the public site, don't save visitors' questions where other visitors could replay them.
            show_investigation(question, run_agent(question, save=not SNAPSHOT))
elif analyze:
    with tab_paste:
        if allowed_to_run():
            text = pasted.strip()
            if len(text) > MAX_PASTE_CHARS:
                st.caption(f"Input trimmed to the last {MAX_PASTE_CHARS:,} characters.")
                text = text[-MAX_PASTE_CHARS:]
            prompt = ("Analyze this output from my Kubernetes cluster and explain what is wrong and how to fix it."
                      + (f" Context from me: {note.strip()}" if note.strip() else "")
                      + f"\n\n```\n{text}\n```")
            show_investigation("Analyze pasted kubectl output", run_agent(prompt, save=not SNAPSHOT, mode="paste"))
