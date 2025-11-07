import os
import json
import pathlib
import pandas as pd
from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL

# Secrets are injected by GitHub Actions:
# Settings → Secrets and variables → Actions → AACT_USER, AACT_PASS
AACT_USER = os.environ["AACT_USER"]
AACT_PASS = os.environ["AACT_PASS"]

# ✅ Build the connection URL safely (handles @, :, /, #, etc. in passwords)
url = URL.create(
    drivername="postgresql+psycopg2",
    username=AACT_USER,
    password=AACT_PASS,
    host="aact-db.ctti-clinicaltrials.org",
    port=5432,
    database="aact",
    query={"sslmode": "require"},  # TLS required
)

engine = create_engine(url, pool_pre_ping=True)

def q(sql, params=None):
    return pd.read_sql(text(sql), engine, params=params or {})

def write_json(name, df, meta):
    outdir = pathlib.Path("public")
    outdir.mkdir(exist_ok=True, parents=True)
    payload = {"meta": meta, "data": json.loads(df.to_json(orient="records"))}
    (outdir / name).write_text(json.dumps(payload, indent=2))

# 0) Meta timestamp (UTC)
as_of = q("select now() as ts").iloc[0]["ts"]
meta = {"as_of_utc": str(as_of)}

# 1) Trials by phase (standardized ordering)
counts_by_phase_sql = """
with phase_map as (
  select case
    when phase = 'Early Phase 1' then 'Early Phase 1'
    when phase = 'Phase 1/Phase 2' then 'Phase 1/2'
    when phase = 'Phase 2/Phase 3' then 'Phase 2/3'
    when phase = 'Phase 1' then 'Phase 1'
    when phase = 'Phase 2' then 'Phase 2'
    when phase = 'Phase 3' then 'Phase 3'
    when phase = 'Phase 4' then 'Phase 4'
    when phase = 'Not Applicable' then 'Not Applicable'
    else coalesce(phase,'Unknown')
  end as phase_std
  from ctgov.studies
)
select phase_std, count(*)::int as n
from phase_map
group by 1
order by case phase_std
  when 'Early Phase 1' then 0
  when 'Phase 1' then 1
  when 'Phase 1/2' then 2
  when 'Phase 2' then 3
  when 'Phase 2/3' then 4
  when 'Phase 3' then 5
  when 'Phase 4' then 6
  when 'Not Applicable' then 7
  else 8 end;
"""
df_phase = q(counts_by_phase_sql)
write_json("counts_by_phase.json", df_phase, meta)

# 2) Phase × Status heatmap
phase_status_sql = """
with base as (
  select
    case
      when phase = 'Early Phase 1' then 'Early Phase 1'
      when phase = 'Phase 1/Phase 2' then 'Phase 1/2'
      when phase = 'Phase 2/Phase 3' then 'Phase 2/3'
      when phase = 'Phase 1' then 'Phase 1'
      when phase = 'Phase 2' then 'Phase 2'
      when phase = 'Phase 3' then 'Phase 3'
      when phase = 'Phase 4' then 'Phase 4'
      when phase = 'Not Applicable' then 'Not Applicable'
      else coalesce(phase,'Unknown')
    end as phase_std,
    coalesce(overall_status,'Unknown') as status
  from ctgov.studies
)
select phase_std, status, count(*)::int as n
from base
group by 1,2;
"""
df_heat = q(phase_status_sql)
write_json("phase_status.json", df_heat, meta)

# 3) Upcoming primary completions (next 12 months)
upcoming_sql = """
select s.nct_id, left(s.brief_title,120) as title,
       case
         when s.phase = 'Early Phase 1' then 'Early Phase 1'
         when s.phase = 'Phase 1/Phase 2' then 'Phase 1/2'
         when s.phase = 'Phase 2/Phase 3' then 'Phase 2/3'
         when s.phase is null then 'Unknown'
         else s.phase
       end as phase_std,
       s.overall_status,
       s.primary_completion_date,
       s.primary_completion_date_type,
       sp.name as lead_sponsor,
       s.enrollment
from ctgov.studies s
left join ctgov.sponsors sp
  on sp.nct_id=s.nct_id and sp.lead_or_collaborator='lead'
where s.primary_completion_date >= current_date
  and s.primary_completion_date < (current_date + interval '12 months')
  and s.overall_status in ('Recruiting','Active, not recruiting','Enrolling by invitation')
order by s.primary_completion_date;
"""
df_upcoming = q(upcoming_sql)
write_json("upcoming_12m.json", df_upcoming, meta)

# 4) Sponsor pipeline (lead-only, top 50 by count)
sponsor_sql = """
with base as (
  select sp.name as sponsor_name,
         case
           when s.phase = 'Early Phase 1' then 'Early Phase 1'
           when s.phase = 'Phase 1/Phase 2' then 'Phase 1/2'
           when s.phase = 'Phase 2/Phase 3' then 'Phase 2/3'
           when s.phase is null then 'Unknown'
           else s.phase
         end as phase_std
  from ctgov.studies s
  join ctgov.sponsors sp on sp.nct_id=s.nct_id
  where sp.lead_or_collaborator='lead'
), top50 as (
  select sponsor_name
  from (select sponsor_name, count(*) as c from base group by sponsor_name) t
  order by c desc limit 50
)
select sponsor_name, phase_std, count(*)::int as n
from base
where sponsor_name in (select sponsor_name from top50)
group by 1,2
order by sponsor_name, phase_std;
"""
df_sponsor = q(sponsor_sql)
write_json("sponsor_pipeline_top50.json", df_sponsor, meta)

# 5) Optional: ready-to-embed Plotly chart
try:
    import plotly.express as px
    html = (pathlib.Path("public") / "counts_by_phase.html")
    fig = px.bar(df_phase, x="phase_std", y="n", title="ClinicalTrials.gov — Trials by Phase")
    fig.update_layout(xaxis_title="Phase", yaxis_title="Number of Trials")
    html.write_text(fig.to_html(include_plotlyjs="cdn", full_html=True))
except Exception as e:
    print("Plotly HTML skipped:", e)

print("✅ Wrote JSON/HTML to ./public")
