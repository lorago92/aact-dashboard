# AACT Dashboard MVP (Daily Auto-Refresh)

This repo generates small JSON feeds (and one HTML chart) from the AACT Postgres database every day and publishes them to **GitHub Pages**. You can embed them on your Wix site via an HTML element or iFrame.

## What it builds
- `counts_by_phase.json` — Trials by standardized phase
- `phase_status.json` — Phase × Status counts
- `upcoming_12m.json` — Next-12-month primary completions (+ sponsor/enrollment)
- `sponsor_pipeline_top50.json` — Lead-sponsor pipeline (top 50 by study count)
- `counts_by_phase.html` — Ready-to-embed Plotly bar chart

All files include a `meta.as_of_utc` timestamp.

## One-time setup
1. Create a GitHub repo and push these files.
2. Add **Actions secrets** (Settings → Secrets and variables → Actions):
   - `AACT_USER` — your AACT DB username (from AACT Connect page)
   - `AACT_PASS` — your AACT DB password
3. Enable **GitHub Pages** → "Deploy from a branch" → select `gh-pages` (created by the action after the first run).
4. The workflow runs daily at **10:15 UTC**. You can also trigger it manually in **Actions → aact-daily-refresh → Run workflow**.

## Wix embedding (two paths)

### A) Fastest — iFrame the ready-made chart
Use the URL to `counts_by_phase.html` on your Pages site:
```
https://<user>.github.io/<repo>/counts_by_phase.html
```
In Wix Editor: **Add → Embed → Embed a site → Website Address** → paste the URL.

### B) JSON + Chart.js snippet
Use the `website_snippets/wix_embed_counts_by_phase_chartjs.html` snippet and replace `YOUR_PAGES_URL` with your GitHub Pages base URL. Embed it via **Embed HTML** in Wix.

## Notes
- AACT updates daily. Scheduling after early morning US time avoids the refresh window.
- Do NOT expose your AACT credentials in any client-side code. They live only in GitHub Actions secrets.
- Extend by adding more queries to `build_dashboard.py` and writing more JSONs to `public/`.
