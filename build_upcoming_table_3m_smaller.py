import os
import pandas as pd
from pathlib import Path
from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL

# === Params you can edit directly ===
HORIZON_MONTHS = 3
FONT_PX = 11  # ← change this to control table + controls font size (px)
STATUS_ACTIVE = (
    'RECRUITING',
    'ACTIVE_NOT_RECRUITING',
    'ENROLLING_BY_INVITATION',
    'NOT_YET_RECRUITING'
)

AACT_USER = os.getenv("AACT_USER")
AACT_PASS = os.getenv("AACT_PASS")
if not AACT_USER or not AACT_PASS:
    raise SystemExit("Missing AACT_USER / AACT_PASS repo secrets.")

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

def q(sql: str) -> pd.DataFrame:
    return pd.read_sql(text(sql), engine)

SQL = f"""
with asof as (select current_date::date d),
base as (
  select
    s.nct_id,
    left(s.brief_title, 180) as title,
    coalesce(s.overall_status,'UNKNOWN') as status,
    case
      when s.phase in ('EARLY_PHASE1','EARLY_PHASE_1')                                  then 'Early Phase 1'
      when s.phase in ('PHASE1','PHASE_1')                                              then 'Phase 1'
      when s.phase in ('PHASE1/PHASE2','PHASE1_PHASE2','PHASE1_2','PHASE_1_2')          then 'Phase 1/2'
      when s.phase in ('PHASE2','PHASE_2')                                              then 'Phase 2'
      when s.phase in ('PHASE2/PHASE3','PHASE2_PHASE3','PHASE2_3','PHASE_2_3')          then 'Phase 2/3'
      when s.phase in ('PHASE3','PHASE_3')                                              then 'Phase 3'
      when s.phase in ('PHASE4','PHASE_4')                                              then 'Phase 4'
      when s.phase in ('NA','NOT_APPLICABLE')                                           then 'Not Applicable'
      else 'Unknown'
    end as phase_std,
    s.enrollment, s.enrollment_type,
    s.last_update_posted_date,
    s.start_date, s.start_date_type,
    s.primary_completion_date, s.primary_completion_date_type,
    s.completion_date, s.completion_date_type,
    (select name
       from ctgov.sponsors sp
       where sp.nct_id=s.nct_id and sp.lead_or_collaborator='lead'
       order by name limit 1) as lead_sponsor
  from ctgov.studies s
  where s.study_type='INTERVENTIONAL'
),
starts as (
  select
    nct_id, title, status, phase_std, enrollment, enrollment_type, last_update_posted_date, lead_sponsor,
    start_date as event_date,
    coalesce(start_date_type,'ESTIMATED') as event_date_type,
    'START'::text as event_type
  from base, asof
  where start_date >= d and start_date < (d + interval '{HORIZON_MONTHS} months')
    and status in {STATUS_ACTIVE}
),
primarys as (
  select
    nct_id, title, status, phase_std, enrollment, enrollment_type, last_update_posted_date, lead_sponsor,
    primary_completion_date as event_date,
    coalesce(primary_completion_date_type,'ESTIMATED') as event_date_type,
    'PRIMARY_COMPLETION'::text as event_type
  from base, asof
  where primary_completion_date >= d and primary_completion_date < (d + interval '{HORIZON_MONTHS} months')
    and status in {STATUS_ACTIVE}
),
completions as (
  select
    nct_id, title, status, phase_std, enrollment, enrollment_type, last_update_posted_date, lead_sponsor,
    completion_date as event_date,
    coalesce(completion_date_type,'ESTIMATED') as event_date_type,
    'COMPLETION'::text as event_type
  from base, asof
  where completion_date >= d and completion_date < (d + interval '{HORIZON_MONTHS} months')
    and status in {STATUS_ACTIVE}
),
u as (
  select * from starts
  union all
  select * from primarys
  union all
  select * from completions
),
iv as (
  select
    i.nct_id,
    string_agg(distinct i.intervention_type, ', ' order by i.intervention_type) as intervention_types,
    string_agg(distinct left(i.name,60), ' · ' order by left(i.name,60))        as interventions
  from ctgov.interventions i
  group by i.nct_id
),
cond as (
  select
    c.nct_id,
    string_agg(distinct left(c.name,60), ', ' order by left(c.name,60)) as conditions
  from ctgov.conditions c
  group by c.nct_id
)
select
  u.event_date::date as event_date,
  u.event_type,
  u.event_date_type,
  u.nct_id, u.title, u.phase_std, u.status, u.enrollment, u.enrollment_type,
  u.lead_sponsor, iv.intervention_types, iv.interventions, cond.conditions,
  u.last_update_posted_date
from u
left join iv   on iv.nct_id=u.nct_id
left join cond on cond.nct_id=u.nct_id
order by u.event_date, u.event_type, u.nct_id
"""

df = q(SQL)
EVENT_ORDER = ["START", "PRIMARY_COMPLETION", "COMPLETION"]
df["event_type"] = pd.Categorical(df["event_type"], categories=EVENT_ORDER, ordered=True)
df = df.sort_values(["event_date", "event_type", "nct_id"]).reset_index(drop=True)

# Write site files
out_dir = Path("public"); out_dir.mkdir(exist_ok=True)
csv_path  = out_dir / "upcoming_trials_next3m_sorted_smaller.csv"
html_path = out_dir / "upcoming_trials_next3m_smaller.html"

df.to_csv(csv_path, index=False)

# ---- Build single clean DataTables HTML (no title, white bg) ----
t = df.copy()
if "enrollment" in t.columns:
    t["enrollment"] = t["enrollment"].map(lambda x: f"{int(x):,}" if pd.notna(x) else "")

# Build ONE table with the proper id/classes (no wrapper table)
table_html = t.to_html(
    index=False,
    escape=True,
    table_id="t",
    classes="display compact"
)

mobile_font_px = max(FONT_PX - 1, 10)

html = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<link rel="stylesheet" href="https://cdn.datatables.net/2.1.7/css/dataTables.dataTables.min.css"/>
<style>
  html, body {{ background:#fff; margin:16px; font-family: system-ui, -apple-system, Segoe UI, Roboto, sans-serif; }}
  table.dataTable {{ width: 100% !important; background:#fff; }}

  /* Smaller font + tighter padding for the DataTable */
  #t, #t th, #t td {{ font-size: {FONT_PX}px; line-height: 1.25; }}
  #t thead th, #t tbody td {{ padding: 4px 6px; }}

  /* Shrink DataTables UI (search, length, info, paging) to match */
  #t_wrapper .dt-search input,
  #t_wrapper .dt-length select,
  #t_wrapper .dt-info,
  #t_wrapper .dt-paging button {{ font-size: {FONT_PX}px; }}

  /* Slightly smaller on narrow screens */
  @media (max-width: 768px) {{
    #t, #t th, #t td {{ font-size: {mobile_font_px}px; }}
  }}
</style>
</head>
<body>
{table_html}
<script src="https://code.jquery.com/jquery-3.7.1.min.js"></script>
<script src="https://cdn.datatables.net/2.1.7/js/dataTables.min.js"></script>
<script>
  new DataTable('#t', {{
    pageLength: 25,
    order: [[0, 'asc'], [1, 'asc']], // event_date then event_type
    scrollX: true,
    language: {{ emptyTable: "" }}   // suppress empty message flicker
  }});
</script>
</body>
</html>
"""

html_path.write_text(html, encoding="utf-8")
print("Wrote:", csv_path)
print("Wrote:", html_path)
