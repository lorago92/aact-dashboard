# build_phase_status_all.py
import os
import pandas as pd
from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL
import plotly.express as px

# --- AACT credentials (stored as GitHub repo secrets) ---
AACT_USER = os.getenv("AACT_USER")
AACT_PASS = os.getenv("AACT_PASS")
if not AACT_USER or not AACT_PASS:
    raise SystemExit("Missing AACT_USER / AACT_PASS repo secrets.")

# --- DB engine ---
url = URL.create(
    "postgresql+psycopg2",
    username=AACT_USER,
    password=AACT_PASS,
    host="aact-db.ctti-clinicaltrials.org",
    port=5432,
    database="aact",
    query={"sslmode": "require"},
)
engine = create_engine(url, pool_pre_ping=True)

def q(sql: str):
    return pd.read_sql(text(sql), engine)

# --- Your exact query/transform/order ---
STATUS_ORDER = [
    "NOT_YET_RECRUITING",
    "RECRUITING",
    "ENROLLING_BY_INVITATION",
    "ACTIVE_NOT_RECRUITING",
    "SUSPENDED",
    "COMPLETED",
    "TERMINATED",
    "WITHDRAWN",
    "UNKNOWN",
]

def reorder_cols(pivot_df, preferred_order):
    cols = list(pivot_df.columns)
    ordered = [c for c in preferred_order if c in cols]
    extras  = [c for c in cols if c not in preferred_order]
    return pivot_df.reindex(columns=ordered + extras, fill_value=0)

SQL_PHASE_STATUS_ALL = """
with base as (
  select
    case
      when phase in ('EARLY_PHASE1','EARLY_PHASE_1')                                  then 'Early Phase 1'
      when phase in ('PHASE1','PHASE_1')                                              then 'Phase 1'
      when phase in ('PHASE1/PHASE2','PHASE1_PHASE2','PHASE1_2','PHASE_1_2')          then 'Phase 1/2'
      when phase in ('PHASE2','PHASE_2')                                              then 'Phase 2'
      when phase in ('PHASE2/PHASE3','PHASE2_PHASE3','PHASE2_3','PHASE_2_3')          then 'Phase 2/3'
      when phase in ('PHASE3','PHASE_3')                                              then 'Phase 3'
      when phase in ('PHASE4','PHASE_4')                                              then 'Phase 4'
      when phase in ('NA','NOT_APPLICABLE')                                           then 'Not Applicable'
      when phase is null                                                              then 'Unknown'
      else 'Unknown'
    end as phase_std,
    coalesce(overall_status,'UNKNOWN') as status
  from ctgov.studies
  where study_type='INTERVENTIONAL'
)
select phase_std, status, count(*)::int as n
from base
group by 1,2
"""

df = q(SQL_PHASE_STATUS_ALL)
pivot = df.pivot_table(index="phase_std", columns="status", values="n", fill_value=0, aggfunc="sum")
pivot = reorder_cols(pivot, STATUS_ORDER)

# 95th percentile cap to improve contrast
z95 = int(pd.Series(pivot.to_numpy().ravel()).quantile(0.95)) if pivot.size else None

fig = px.imshow(
    pivot,
    aspect="auto",
    color_continuous_scale="Blues",
    zmax=z95,  # comment out to use full dynamic range
    text_auto=True,
)
fig.update_layout(template="plotly_white", plot_bgcolor="white", paper_bgcolor="white",
                  margin=dict(l=60, r=20, t=60, b=60),
                  title="All Interventional Trials — Phase × Status (ordered lifecycle)")
fig.update_traces(texttemplate="%{z:,}", textfont={"size":11})
fig.update_xaxes(side="top")
fig.update_yaxes(title_text="Phase")

# Output to /public for GitHub Pages
os.makedirs("public", exist_ok=True)
fig.write_html("public/phase_status_all.html", include_plotlyjs="cdn", full_html=True)

# Optional landing page
with open("public/index.html","w") as f:
    f.write("""<!doctype html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>AACT Dashboard</title></head>
<body style="font-family:system-ui,Segoe UI,Arial,sans-serif;padding:24px;">
<h1>AACT Dashboard</h1>
<ul><li><a href="phase_status_all.html">All Interventional Trials — Phase × Status</a></li></ul>
</body></html>""")

print("Wrote: public/phase_status_all.html and public/index.html")
