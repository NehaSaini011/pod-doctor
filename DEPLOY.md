# Deploying Pod Doctor to a public URL

You'll get a link like `https://pod-doctor.streamlit.app`. Evaluators can open it and ask the agent anything.

**How the deployed version works.** A public website can't reach the kind cluster on your laptop. So you record a **snapshot** of the real cluster once, and the website's tools read from that recording. **The agent loop, the Gemini calls and the RAG search are all live.** Only the cluster data is recorded, and the app shows a banner saying so.

Hosting is on **Streamlit Community Cloud**, which is free. Total time is about 30–40 minutes once the project runs locally.

---

## Step 1: Run it locally and rehearse (do this first)

```powershell
python scripts/cluster.py up        # wait until 6 services are broken
streamlit run app.py
```
Click **"Diagnose every unhealthy pod..."** and 2–3 other presets. Every run is saved in `runs/`, and those saved runs will also appear under **Replay** on the website, which is your backup if the API is slow on the day.

## Step 2: Record the snapshot

With the cluster still running:
```powershell
python scripts/snapshot.py
```
This creates `data/cluster_snapshot.json` (secret values are redacted). Test the website version locally:
```powershell
$env:CLUSTER_MODE='snapshot'; streamlit run app.py
```
You should see the 📸 banner and the same 7 pods. Ask a question to confirm the agent works. Close the terminal afterwards so `CLUSTER_MODE` resets.

## Step 3: Push to GitHub

Create a **new public repository** called `pod-doctor` at https://github.com/new (leave "Add README" unticked). Then, in the project folder:
```powershell
git init
git add .
git status            # CHECK: .env must NOT be in the list (it holds your API key)
git commit -m "Pod Doctor: agentic Kubernetes troubleshooting assistant"
git branch -M main
git remote add origin https://github.com/NehaSaini011/pod-doctor.git
git push -u origin main
```
If `.env` appears in `git status`, stop and make sure `.gitignore` is in the folder.

## Step 4: Deploy on Streamlit Community Cloud

1. Go to https://share.streamlit.io and sign in with GitHub.
2. Click **Create app**, then **"Yup, I have an app"**.
3. Repository `NehaSaini011/pod-doctor`, branch `main`, main file `app.py`.
4. Pick your app URL (for example `pod-doctor-neha`).
5. Open **Advanced settings** and paste this into **Secrets**, replacing the values:
   ```toml
   GEMINI_API_KEY = "your-gemini-key"
   GEMINI_MODEL = "gemini-3.6-flash"
   FALLBACK_MODELS = "gemini-3.8-flash,gemini-3.5-flash-lite"
   CLUSTER_MODE = "snapshot"
   APP_PASSWORD = "pick-an-access-code"
   MAX_RUNS_PER_SESSION = "5"
   ```
6. Click **Deploy**. The first build takes a few minutes.

**Why the access code:** your Gemini key pays for every question. Without a code, anyone with the link could use up your free quota. Without the code, visitors can still watch Replays; the live agent needs it. Put the code on your title slide on the day.

## Step 5: Before the evaluation

- **Open the URL 10–15 minutes early.** Free apps go to sleep when nobody uses them and take a minute to wake up.
- **Test it in an incognito window**, including the access code, and run one question.
- **Keep the laptop version ready.** If the panel asks "is this live?", you can show the real cluster too. That's a strong answer.

## Updating the site

Any `git push` redeploys automatically. If you change the cluster, run `python scripts/snapshot.py` again, then commit and push.

## If something goes wrong

| Problem | Fix |
|---|---|
| "No cluster snapshot found" | `data/cluster_snapshot.json` wasn't pushed. Run Step 2, then `git add data` and push. |
| ❌ Gemini API key in the sidebar | Secrets missing or misspelled. Go to App settings, then Secrets. |
| Model not found / 429 errors | Change `GEMINI_MODEL` in Secrets to a current Flash model; for 429, wait a minute. |
| App shows an error after a push | Click **Manage app** (bottom right of the app) to see the logs. |
