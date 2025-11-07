import os
import pandas as pd
from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL
import plotly.express as px

# ---- Controls ----
HORIZON_MONTHS = int(os.getenv("UPCOMING_HORIZON_MONTHS", "12"))
PHASE_ORDER = [
    "Early Phase 1","Phase 1","Phase 1/2","Phase 2","Phase 2/3",
    "Phase 3","Phase 4","Not Applicable","Unknown"
]
STATUS_ACTIVE = [
    "NOT_YET_RECRUITING","RECRUITING","ENROLLING_BY_INVITATION","ACTIVE_NOT_RECRUITING"
]
STATUS_COLORS = {
    "NOT_YET_RECRUITING": "#deebf7",
    "RECRUITING": "#9ecae1",
    "ENROLLING_BY_INVITATION": "#6baed6",
    "ACTIVE_NOT_RECRUITING": "#2171b5",
}

# ---- AACT connection from repo secrets ----
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

def q(sql: str):
    return pd.read_sql(text(sql), engine)

SQL_TEMPLATE = """
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
      else 'Unknown'
    end as phase_std,
    coalesce(overall_status,'UNKNOWN') as status,
    start_date, primary_completion_date, completion_date
  from ctgov.studies
  where study_type='INTERVENTIONAL'
)
select phase_std, status, count(*)::int as n
from base
where {date_field} >= current_date
  and {date_field} < (current_date + interval '{months} months')
  and status in ('RECRUITING','ACTIVE_NOT_RECRUITING','ENROLLING_BY_INVITATION','NOT_YET_RECRUITING')
group by 1,2
"""

def counts_by_phase_status(date_field: str) -> pd.DataFrame:
    sql = SQL_TEMPLATE.format(date_field=date_field, months=HORIZON_MONTHS)
    return q(sql)

def bar_chart_for(date_field: str):
    df = counts_by_phase_status(date_field)
    if df.empty:
        return None

    df["phase_std"] = pd.Categorical(df["phase_std"], categories=PHASE_ORDER, ordered=True)
    df["status"]    = pd.Categorical(df["status"],    categories=STATUS_ACTIVE, ordered=True)

    piv = df.pivot_table(index="phase_std", columns="status", values="n", fill_value=0, aggfunc="sum")
    piv = piv.reindex(index=PHASE_ORDER, fill_value=0)[STATUS_ACTIVE]
    plot_df = piv.reset_index().melt(id_vars="phase_std", var_name="status", value_name="count")

    fig = px.bar(
        plot_df,
        x="phase_std", y="count", color="status",
        category_orders={"phase_std": PHASE_ORDER, "status": STATUS_ACTIVE},
        color_discrete_map=STATUS_COLORS,
    )
    fig.update_layout(
        title=None,                 # no title (clean embed)
        barmode="stack",
        barnorm="",                 # absolute counts
        template="plotly_white",
        plot_bgcolor="white", paper_bgcolor="white",
        margin=dict(l=60,r=20,t=10,b=60),
        legend_title_text="Status",
        yaxis_title=f"Trials (next {HORIZON_MONTHS} months)",
        xaxis_title="Phase",
        hovermode="x unified",
    )
    # totals on top of each stack
    totals = piv.sum(axis=1).values
    fig.add_scatter(
        x=PHASE_ORDER, y=totals, mode="text",
        text=[f"{v:,}" for v in totals], textposition="top center",
        showlegend=False
    )
    return fig

os.makedirs("public", exist_ok=True)

# Build three pages
charts = [
    ("start_date",                "public/upcoming_starts_bars.html"),
    ("primary_completion_date",   "public/upcoming_primary_bars.html"),
    ("completion_date",           "public/upcoming_completion_bars.html"),
]
for date_field, outfile in charts:
    fig = bar_chart_for(date_field)
    if fig is None:
        print(f"No rows for {date_field}, skipping {outfile}")
        continue
    fig.write_html(
        outfile,
        include_plotlyjs="cdn",
        full_html=True,
        config={"displaylogo": False, "modeBarButtonsToRemove": ["toImage","lasso2d","select2d"]}
    )
    print("Wrote:", outfile)
