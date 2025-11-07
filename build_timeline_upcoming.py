import os
import pandas as pd
from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL
import plotly.express as px

# --- Config ---
HORIZON_MONTHS = int(os.getenv("TIMELINE_HORIZON_MONTHS", "12"))
STATUS_ACTIVE = (
    'RECRUITING',
    'ACTIVE_NOT_RECRUITING',
    'ENROLLING_BY_INVITATION',
    'NOT_YET_RECRUITING'
)

# --- AACT creds from repo secrets ---
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
    query={"sslmode":"require"},
)
engine = create_engine(url, pool_pre_ping=True)

def q(sql: str):
    return pd.read_sql(text(sql), engine)

SQL = f"""
with base as (
  select
    s.nct_id,
    coalesce(s.overall_status,'UNKNOWN') as status,
    s.start_date,
    s.primary_completion_date,
    s.completion_date
  from ctgov.studies s
  where s.study_type='INTERVENTIONAL'
),
bounds as (
  select date_trunc('month', current_date)::date                        as start_month,
         date_trunc('month', (current_date + interval '{HORIZON_MONTHS} months'))::date as end_month
),
months as (
  select generate_series(b.start_month, b.end_month, interval '1 month')::date as month
  from bounds b
),
starts as (
  select date_trunc('month', start_date)::date as month, count(*)::int as n
  from base
  where start_date >= current_date
    and start_date <  (current_date + interval '{HORIZON_MONTHS} months')
    and status in {STATUS_ACTIVE}
  group by 1
),
primary_compl as (
  select date_trunc('month', primary_completion_date)::date as month, count(*)::int as n
  from base
  where primary_completion_date >= current_date
    and primary_completion_date <  (current_date + interval '{HORIZON_MONTHS} months')
    and status in {STATUS_ACTIVE}
  group by 1
),
compl as (
  select date_trunc('month', completion_date)::date as month, count(*)::int as n
  from base
  where completion_date >= current_date
    and completion_date <  (current_date + interval '{HORIZON_MONTHS} months')
    and status in {STATUS_ACTIVE}
  group by 1
)
select m.month,
       coalesce(s.n,0)  as starts,
       coalesce(p.n,0)  as primary_completions,
       coalesce(c.n,0)  as completions
from months m
left join starts        s on s.month=m.month
left join primary_compl p on p.month=m.month
left join compl         c on c.month=m.month
order by m.month
"""

tl = q(SQL)
long = tl.melt(id_vars="month", var_name="event", value_name="count")
fig = px.line(long, x="month", y="count", color="event", markers=True)
fig.update_layout(
    template="plotly_white",
    plot_bgcolor="white", paper_bgcolor="white",
    margin=dict(l=60,r=20,t=10,b=60),
    hovermode="x unified",
    legend_title_text="Event",
    yaxis_title="Trials per month"
)
fig.update_xaxes(dtick="M1", tickformat="%b %Y", ticklabelmode="period")

os.makedirs("public", exist_ok=True)
fig.write_html(
    "public/timeline_upcoming.html",
    include_plotlyjs="cdn",
    full_html=True,
    config={"displaylogo": False, "modeBarButtonsToRemove": ["toImage","lasso2d","select2d"]}
)
print("Wrote: public/timeline_upcoming.html")
