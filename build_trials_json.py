import json
import os
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL

HORIZON_MONTHS = int(os.getenv("TRIALS_JSON_HORIZON_MONTHS", "12"))
STATUS_ACTIVE = (
    'RECRUITING',
    'ACTIVE_NOT_RECRUITING',
    'ENROLLING_BY_INVITATION',
    'NOT_YET_RECRUITING',
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

SQL = f"""
with asof as (select current_date::date d),
lead_sp as (
  select distinct on (nct_id)
    nct_id, name as lead_sponsor, agency_class as sponsor_class
  from ctgov.sponsors
  where lead_or_collaborator='lead'
  order by nct_id, name
),
iv as (
  select
    nct_id,
    string_agg(distinct intervention_type, ', ' order by intervention_type) as intervention_types,
    string_agg(distinct left(name, 80), ' · ' order by left(name, 80))      as interventions
  from ctgov.interventions group by nct_id
),
cond as (
  select
    nct_id,
    string_agg(distinct left(name, 80), ', ' order by left(name, 80)) as conditions
  from ctgov.conditions group by nct_id
),
base as (
  select
    s.nct_id,
    left(s.brief_title, 240) as title,
    coalesce(s.overall_status, 'UNKNOWN') as status,
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
    end as phase,
    s.enrollment, s.enrollment_type,
    s.last_update_posted_date,
    s.start_date, s.start_date_type,
    s.primary_completion_date, s.primary_completion_date_type,
    s.completion_date, s.completion_date_type
  from ctgov.studies s, asof
  where s.study_type = 'INTERVENTIONAL'
    and s.overall_status in {STATUS_ACTIVE}
    and (
         (s.start_date              >= asof.d and s.start_date              < asof.d + interval '{HORIZON_MONTHS} months')
      or (s.primary_completion_date >= asof.d and s.primary_completion_date < asof.d + interval '{HORIZON_MONTHS} months')
      or (s.completion_date         >= asof.d and s.completion_date         < asof.d + interval '{HORIZON_MONTHS} months')
    )
)
select
  b.nct_id, b.title, b.status, b.phase,
  lead_sp.lead_sponsor, lead_sp.sponsor_class,
  b.enrollment, b.enrollment_type,
  b.start_date, b.start_date_type,
  b.primary_completion_date, b.primary_completion_date_type,
  b.completion_date, b.completion_date_type,
  b.last_update_posted_date,
  iv.intervention_types, iv.interventions,
  cond.conditions
from base b
left join lead_sp on lead_sp.nct_id = b.nct_id
left join iv      on iv.nct_id      = b.nct_id
left join cond    on cond.nct_id    = b.nct_id
order by b.nct_id
"""

df = pd.read_sql(text(SQL), engine)

for col in (
    "start_date",
    "primary_completion_date",
    "completion_date",
    "last_update_posted_date",
):
    s = pd.to_datetime(df[col], errors="coerce")
    df[col] = s.dt.strftime("%Y-%m-%d").where(s.notna(), None)

df["enrollment"] = df["enrollment"].map(
    lambda x: int(x) if pd.notna(x) else None
)

rows = df.where(pd.notna(df), None).to_dict(orient="records")
for r in rows:
    r["url"] = f"https://clinicaltrials.gov/study/{r['nct_id']}"

out = {
    "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    "horizon_months": HORIZON_MONTHS,
    "row_count": len(rows),
    "rows": rows,
}

out_dir = Path("public")
out_dir.mkdir(exist_ok=True)
out_path = out_dir / "trials.json"
out_path.write_text(json.dumps(out, separators=(",", ":")), encoding="utf-8")
print(f"Wrote: {out_path}  ({len(rows)} rows)")
