# How Pod Doctor works: presenter's guide

Read this before the evaluation. It explains each part in the order you'll present it, then lists the questions evaluators are likely to ask, with answers.

---

## 1. The one-line pitch

> "When a Kubernetes service breaks, an on-call engineer runs the same investigation every time: list pods, describe the broken one, read the logs, check events, look up the runbook. I built an AI agent that does that investigation itself and hands back a report with evidence."

Three concepts from the internship, all in one project:

| Concept | Where it is in the project |
|---|---|
| **Agentic AI** | `agent.py`: the LLM decides which tools to call, in which order, and when to stop |
| **Tool / function calling** | `tools.py`: 6 tools described with JSON schemas, run by our code |
| **RAG** | `rag.py` + `knowledge_base/`: runbooks embedded as vectors, retrieved by similarity, cited in the answer |
| **GenAI** | the final incident report is generated, but grounded in tool output and retrieved runbooks |

---

## 2. The agent loop, step by step (`agent.py`)

**Data structures**
- `contents`: the conversation history list sent to Gemini each turn (user question, model turns, tool results)
- `TOOLS`: a dictionary mapping tool name → {Python function, description, JSON parameter schema}
- `events`: a list of everything that happened (thoughts, tool calls, results, final answer); shown in the UI and saved to `runs/`

**Algorithm**
1. Put the user's question into `contents` as the first message.
2. Set `step = 1`.
3. Send `contents` + the system prompt + all tool declarations from `TOOLS` to Gemini.
4. Read the parts of the model's reply.
5. If the reply contains **no function calls** → it's the final report. Record it in `events` and go to step 11.
6. Append the model's reply to `contents`. This keeps the history intact and preserves the model's thought signatures.
7. For each function call in the reply:
   1. Record the call (name + arguments) in `events`.
   2. Look the name up in `TOOLS` and run the matching Python function with the arguments.
   3. If the function fails, wrap the error as data `{"error": ...}` instead of crashing, so the model can see its mistake and try something else.
   4. Record the result in `events`.
8. Append all tool results to `contents` as one message (each result tagged with its call's id).
9. Increase `step` by 1.
9b. (Self-review) When the model gives a draft report in step 5, first check it with `review_report`. If it has gaps and hasn't been sent back before, append the draft plus a message listing the gaps to `contents` and go back to step 3, instead of finishing.
10. If `step` is at most `MAX_AGENT_STEPS` (15), go to step 3. Otherwise stop with "step limit reached". This guard stops infinite loops.
11. Save `events` to `runs/<timestamp>.json` for replay.

**Example trace** ("Why is report-worker restarting?")

| Step | Model decides | Tool result (short) | What the model learns |
|---|---|---|---|
| 1 | `list_pods(shop)` | report-worker: CrashLoopBackOff, 5 restarts, last termination OOMKilled exit 137 | It's being killed, not crashing on its own |
| 2 | `describe_pod(report-worker-...)` + `get_resource(deployment, report-worker)` *(in parallel)* | Limits memory 100Mi; args `--vm-bytes 250M` | It needs ~250 MiB, but the limit is 100 MiB |
| 3 | `search_runbooks("OOMKilled exit code 137 memory limit")` | `oomkilled.md` (top match) | The documented fix: raise the limit above peak usage |
| 4 | *(no tool call)* | — | Writes the report: evidence, root cause, YAML fix, cites `oomkilled.md` |

The real trace will differ a little each run. That's normal, because the model chooses its own path. Talk the panel through whatever appears on screen.

---

## 3. RAG, step by step (`rag.py`)

**Data structures:** `docs` (list of runbooks: file, title, text), `vectors` (matrix with one row per runbook, each row normalised to length 1).

**Building the index (once, at startup)**
1. Read every `.md` file in `knowledge_base/` into `docs`.
2. For each runbook, call the Gemini embedding model (`gemini-embedding-001`, task type `RETRIEVAL_DOCUMENT`) to get a vector of numbers that represents its meaning.
3. Normalise each vector to length 1 and stack them into `vectors`.
4. Save `vectors` to `.cache/`, so later runs don't pay for the embeddings again.

**Searching (every time the agent calls `search_runbooks`)**
1. Embed the query with task type `RETRIEVAL_QUERY` and normalise it.
2. Compute `scores = vectors · query`. With unit-length vectors, a dot product equals cosine similarity.
3. Sort the scores in descending order and return the top k runbooks (default 2), with their similarity scores.

**Fallback:** with no API key or no network, we build TF-IDF keyword vectors (scikit-learn) instead. The same cosine search still works, just with keyword matching rather than meaning.

**Why it's RAG:** the LLM isn't trusted to recall Kubernetes fixes from its training data. It **retrieves** the team's own documented procedure and **generates** its answer from it, citing the file. That reduces hallucination and lets a team plug in its own runbooks.

---

## 4. Safety (evaluators like hearing this)

1. **Read-only by construction:** the code only ever builds `get`, `describe` and `logs` commands. There's no tool that can change the cluster.
2. **No command injection:** commands run as argument lists with no shell, and every name from the LLM must match the Kubernetes naming regex. `x; rm -rf /` gets rejected.
3. **Pinned context:** every call uses `--context kind-pod-doctor`, so the agent can't touch any other cluster.
4. **No secret leakage:** for Secrets, only key names are returned, never values.
5. **Bounded:** a 15-step limit, output truncation, and automatic retry with back-off on API rate limits.
6. **Human in the loop:** the agent *recommends* fixes; a person applies them.

---

## 5. Likely questions, with answers

**Q: Isn't this just a script that runs kubectl commands?**
No. A script follows a fixed path. Here the LLM picks the next tool based on what it has seen so far. For an ImagePullBackOff it skips logs (there are none) and reads events. For a crash loop it reads the *previous* container's logs. For orders-api, which looks "Running", it has to notice 0/1 Ready and inspect the probe. None of that branching is written in the code.

**Q: Why didn't you use LangChain or LangGraph?**
I wanted every line to be explainable. The loop is about 60 lines using Gemini's native function calling, so there's no hidden framework behaviour. LangGraph would help with multi-agent graphs or long-running state, which this project doesn't need. It would be my next step if I added an approval step before applying fixes.

**Q: How do you stop the agent from hallucinating a root cause?**
Three ways. The system prompt requires every root cause to quote evidence from tool output. The runbooks give it a grounded fix procedure. And the trace is shown to the user, so every claim can be checked against the raw tool results.

**Q: How do you know the runbook citations are real?**
After every report, the app runs a citation check (`check_citations` in `agent.py`). It collects every runbook file the report cites and compares them with the runbooks `search_runbooks` actually returned during that run. If the model names a source it never retrieved, the UI shows a warning. Grounding is verified in code, not just requested in the prompt.

**Q: What happens if the agent cuts corners?**
There's a self-review step (`review_report` in `agent.py`), a pattern known as *reflection*. When the agent produces a draft report, code checks it for gaps: findings with no retrieved runbook, citations that were never retrieved, and restarting pods whose crash logs were never read. If it finds any, the draft goes back to the agent with the list of problems, and the agent uses its tools again and rewrites the report (at most once, so it can't loop forever). I added it after noticing in testing that the smallest model skipped runbook searches for 4 of 6 services.

**Q: Isn't the demo hard-coded to your 6 broken services?**
No, and the app can prove it. In the **🧪 Break it yourself** panel, *you* pick any Deployment and one of 7 faults. The panel changes the Deployment, and the agent is never told what changed: no label, no annotation, nothing it can read. It has to find the fault from the cluster itself. The panel is not one of the agent's tools: the agent stays read-only, and the panel refuses to run on any cluster except the local demo one (`CHAOS_CONTEXT`), so it can never touch a real cluster. You can also point the agent at any other cluster or namespace from the sidebar, or paste output from your own cluster in the second tab.

**Q: How does "paste your own output" work, and why not let people connect their cluster?**
It's the same agent loop in a different *mode*: the only tool it gets is `search_runbooks`, and its instructions say the pasted text is its only evidence (and to ignore any instructions inside it, since that is untrusted data: a prompt-injection defence). Letting visitors connect their own cluster would mean uploading cluster credentials to a public website, which is a serious security risk, so I deliberately didn't build that.

**Q: What is function calling?**
Each tool is declared to the model with a name, a description and a JSON schema for its parameters. Instead of text, the model can reply with a structured request like `{"name": "describe_pod", "args": {"pod_name": "..."}}`. My code executes it and sends the result back. The model never runs anything itself.

**Q: What are embeddings and why cosine similarity?**
An embedding is a vector that represents the meaning of a text, so texts with similar meanings end up close together. Cosine similarity measures the angle between two vectors, which ignores length, so a long runbook and a short query can still match. Because I normalise every vector, cosine similarity is just a dot product.

**Q: What's the difference between readiness and liveness probes?** (the orders-api case)
A failed **readiness** probe removes the pod from Service traffic but doesn't restart it, which is why orders-api looks "Running" with 0 restarts. A failed **liveness** probe restarts the container.

**Q: Why exit code 137?**
128 + 9: the process got SIGKILL. Together with the reason `OOMKilled`, it means the kernel killed it for exceeding its memory limit.

**Q: Is the deployed website really live?**
The agent is. Every question triggers real Gemini calls, real tool calls and real RAG retrieval, and the agent picks its own path each time. What's recorded is the *cluster data* the tools read. A public website can't reach the kind cluster on my laptop, so `scripts/snapshot.py` records the output of every read-only kubectl command the tools can run, and the deployed tools read that recording. Same code, one setting (`CLUSTER_MODE`) switches between the two. I can also show it against the live cluster on my laptop.

**Q: Limitations?**
- It depends on an external LLM API: there's latency, a free-tier rate limit, and the cluster data is sent to the model provider. For production, I'd use a self-hosted model or a private endpoint.
- The runbook base is small (12 documents) and chunked per file. A real one would need section-level chunking and a vector database.
- It only diagnoses; it doesn't fix. Auto-remediation would need an approval step.
- It covers one namespace at a time, with no metrics such as CPU or memory over time.

**Q: Future work?**
A "propose fix → human approves → apply → verify" loop; Prometheus metrics as a tool; triggering the agent automatically from an alert; evaluating accuracy over many injected failures.

**Q: How did you evaluate it?**
There are 6 injected failures with known root causes, listed in the cluster table in the README, so I can check whether the agent's report names the right root cause for each one. I also wrote 36 offline tests: the tools against simulated kubectl output, input validation, Secret redaction, retrieval returning the correct runbook for each failure type, and snapshot mode.

---

## 6. Words to use when presenting
*agent loop, tool/function calling, ReAct (reason + act), grounding, retrieval-augmented generation, embeddings, cosine similarity, read-only tools, human-in-the-loop, root cause analysis, blast radius, runbook.*
