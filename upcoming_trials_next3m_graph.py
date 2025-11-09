import os
import pandas as pd
from pathlib import Path
from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL

# === Params you can edit directly ===
HORIZON_MONTHS   = 3
FONT_PX          = 11     # table + header + controls font size (px)
TOP_SPONSORS     = 12     # default "Top N sponsors" in the stacked chart
TOP_INTERVENTIONS= 20     # default "Top N intervention types"
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
csv_path  = out_dir / "upcoming_trials_next3m_sorted.csv"
html_path = out_dir / "upcoming_trials_next3m_graph.html"
df.to_csv(csv_path, index=False)

# ---- Build DataTable HTML + Interactive Charts ----
t = df.copy()
if "enrollment" in t.columns:
    t["enrollment"] = t["enrollment"].map(lambda x: f"{int(x):,}" if pd.notna(x) else "")

table_html = t.to_html(
    index=False,
    escape=True,
    table_id="t",
    classes="display compact"
)

mobile_font_px = max(FONT_PX - 1, 10)

# Predeclare a known order for nicer chart grouping
PHASE_ORDER  = ["Early Phase 1", "Phase 1", "Phase 1/2", "Phase 2", "Phase 2/3", "Phase 3", "Phase 4", "Not Applicable", "Unknown"]
STATUS_ORDER = ["RECRUITING", "ACTIVE_NOT_RECRUITING", "ENROLLING_BY_INVITATION", "NOT_YET_RECRUITING", "SUSPENDED", "TERMINATED", "COMPLETED", "WITHDRAWN", "UNKNOWN"]

html = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<link rel="stylesheet" href="https://cdn.datatables.net/2.1.7/css/dataTables.dataTables.min.css"/>
<style>
  html, body {{ background:#fff; margin:16px; font-family: system-ui, -apple-system, Segoe UI, Roboto, sans-serif; }}
  table.dataTable {{ width: 100% !important; background:#fff; }}

  /* Base table font + padding */
  #t, #t th, #t td {{ font-size: {FONT_PX}px; line-height: 1.25; }}
  #t thead th, #t tbody td {{ padding: 4px 6px; }}

  /* Cloned header/body when scrollX is on */
  #t_wrapper .dt-scroll-head table,
  #t_wrapper .dt-scroll-head th,
  #t_wrapper .dt-scroll-head td,
  #t_wrapper .dt-scroll-body table,
  #t_wrapper .dt-scroll-body th,
  #t_wrapper .dt-scroll-body td {{
    font-size: {FONT_PX}px;
    line-height: 1.25;
  }}
  #t_wrapper .dt-scroll-head th,
  #t_wrapper .dt-scroll-body td {{ padding: 4px 6px; }}

  /* Controls */
  #t_wrapper .dt-search input,
  #t_wrapper .dt-length select,
  #t_wrapper .dt-info,
  #t_wrapper .dt-paging button {{ font-size: {FONT_PX}px; }}

  /* Layout: controls + charts grid */
  .toolbar {{
    display: flex; flex-wrap: wrap; gap: 12px; align-items: center; margin: 8px 0 16px;
    font-size: {FONT_PX}px;
  }}
  .charts {{
    display: grid; gap: 16px; margin-top: 16px;
    grid-template-columns: repeat(12, 1fr);
  }}
  .card {{
    grid-column: span 12; background: #fff; border: 1px solid #eee; border-radius: 12px; padding: 12px;
  }}
  .card h3 {{ margin: 0 0 8px; font-size: {FONT_PX + 2}px; }}
  @media (min-width: 900px) {{
    #chart_phase_sponsor.card {{ grid-column: span 12; }}
    #chart_phase_total.card, #chart_status_total.card {{ grid-column: span 6; }}
    #chart_intervention_total.card {{ grid-column: span 12; }}
  }}

  button, select, input[type="number"] {{
    font-size: {FONT_PX}px; padding: 4px 8px; border-radius: 8px; border: 1px solid #ddd; background: #fff;
  }}

  /* Make sponsor cells appear clickable for quick filter */
  .sponsor-cell {{ cursor: pointer; text-decoration: underline; text-underline-offset: 2px; }}
</style>
</head>
<body>

<div class="toolbar">
  <button id="resetFiltersBtn" title="Clear all table filters">Reset filters</button>
  <label>Top sponsors: <input id="topSponsors" type="number" min="3" max="50" value="{TOP_SPONSORS}" /></label>
  <label>Top intervention types: <input id="topInterventions" type="number" min="5" max="100" value="{TOP_INTERVENTIONS}" /></label>
</div>

{table_html}

<div class="charts">
  <div id="chart_phase_sponsor" class="card">
    <h3>Trials per Phase per Lead Sponsor (Top-N by total count)</h3>
    <div id="chart_phase_sponsor_plot"></div>
  </div>
  <div id="chart_phase_total" class="card">
    <h3>Total Trials per Phase</h3>
    <div id="chart_phase_total_plot"></div>
  </div>
  <div id="chart_status_total" class="card">
    <h3>Total Trials per Status</h3>
    <div id="chart_status_total_plot"></div>
  </div>
  <div id="chart_intervention_total" class="card">
    <h3>Intervention Type Counts</h3>
    <div id="chart_intervention_total_plot"></div>
  </div>
</div>

<script src="https://code.jquery.com/jquery-3.7.1.min.js"></script>
<script src="https://cdn.datatables.net/2.1.7/js/dataTables.min.js"></script>
<script src="https://cdn.plot.ly/plotly-2.35.2.min.js"></script>
<script>
  // Initialize DataTable
  const dt = new DataTable('#t', {{
    pageLength: 25,
    order: [[0, 'asc'], [1, 'asc']],
    scrollX: true,
    language: {{ emptyTable: "" }},
    autoWidth: false,
    createdRow: function(row, data, dataIndex) {{
      // Add 'sponsor-cell' class to Lead Sponsor cell for click-to-filter
      const sponsorIdx = getColumnIndex('lead_sponsor');
      if (sponsorIdx !== -1) {{
        row.cells[sponsorIdx].classList.add('sponsor-cell');
      }}
    }}
  }});

  // Ensure widths recalculated after styles load
  window.addEventListener('load', () => dt.columns.adjust());
  setTimeout(() => dt.columns.adjust(), 0);

  // Column index lookup by header text
  function getColumnIndex(name) {{
    name = String(name).trim();
    let idx = -1;
    dt.columns().every(function(i) {{
      const h = (this.header().textContent || '').trim();
      if (h === name) idx = i;
    }});
    return idx;
  }}

  // Utility: text cleanup
  function clean(v) {{
    if (v === null || v === undefined) return '';
    let s = String(v);
    // Strip any HTML entities (DataTables may escape)
    const div = document.createElement('div');
    div.innerHTML = s;
    return (div.textContent || div.innerText || '').trim();
  }}

  // Extract filtered rows as array of objects keyed by column names
  function getFilteredRows() {{
    const headers = [];
    dt.columns().every(function(i) {{ headers.push((this.header().textContent || '').trim()); }});
    const arr = dt.rows({{ search: 'applied' }}).data().toArray();
    return arr.map(row => {{
      const obj = {{}};
      headers.forEach((h, i) => obj[h] = clean(row[i]));
      return obj;
    }});
  }}

  // Count helpers
  function countBy(arr, key) {{
    const m = new Map();
    for (const r of arr) {{
      const k = clean(r[key] || '');
      m.set(k, (m.get(k) || 0) + 1);
    }}
    return m;
  }}

  // Count by lead_sponsor then phase
  function countSponsorPhase(arr) {{
    const m = new Map(); // sponsor -> Map(phase -> count)
    for (const r of arr) {{
      const sponsor = clean(r['lead_sponsor'] || 'Unknown') || 'Unknown';
      const phase   = clean(r['phase_std'] || 'Unknown') || 'Unknown';
      if (!m.has(sponsor)) m.set(sponsor, new Map());
      const inner = m.get(sponsor);
      inner.set(phase, (inner.get(phase) || 0) + 1);
    }}
    return m;
  }}

  // Count intervention types (split aggregated string)
  function countInterventionTypes(arr) {{
    const m = new Map();
    for (const r of arr) {{
      const raw = clean(r['intervention_types'] || '');
      if (!raw) continue;
      raw.split(',').forEach(part => {{
        const t = part.trim();
        if (!t) return;
        m.set(t, (m.get(t) || 0) + 1);
      }});
    }}
    return m;
  }}

  // Draw charts based on current filter state
  function updateCharts() {{
    const rows = getFilteredRows();

    // Phase per Sponsor (Top-N sponsors)
    const topN = Number(document.getElementById('topSponsors').value || {TOP_SPONSORS});
    const sponsorPhase = countSponsorPhase(rows);
    // Sponsor totals for ranking
    const sponsorTotals = Array.from(sponsorPhase.entries()).map(([s, mp]) => [s, Array.from(mp.values()).reduce((a,b)=>a+b,0)]);
    sponsorTotals.sort((a,b)=> b[1]-a[1]);
    const topSponsors = sponsorTotals.slice(0, Math.max(1, topN)).map(d=>d[0]);

    const PHASE_ORDER = {PHASE_ORDER};
    const sponsorsX = topSponsors;

    const tracesSponsorPhase = PHASE_ORDER.map(phase => {{
      const y = sponsorsX.map(s => {{
        const inner = sponsorPhase.get(s);
        return inner ? (inner.get(phase) || 0) : 0;
      }});
      return {{
        type: 'bar',
        name: phase,
        x: sponsorsX,
        y: y
      }};
    }});

    Plotly.react('chart_phase_sponsor_plot', tracesSponsorPhase, {{
      barmode: 'stack',
      xaxis: {{ automargin: true }},
      yaxis: {{ title: 'Trials', rangemode: 'tozero' }},
      margin: {{ t: 10, r: 10, b: 60, l: 50 }},
      showlegend: true
    }});

    // Total per Phase
    const byPhase = countBy(rows, 'phase_std');
    const phaseCats = {PHASE_ORDER}.filter(p => byPhase.has(p)).concat(
      Array.from(byPhase.keys()).filter(p => !{PHASE_ORDER}.includes(p))
    );
    const phaseVals = phaseCats.map(p => byPhase.get(p) || 0);

    Plotly.react('chart_phase_total_plot', [{{
      type: 'bar',
      x: phaseCats,
      y: phaseVals
    }}], {{
      xaxis: {{ tickangle: -30 }},
      yaxis: {{ title: 'Trials', rangemode: 'tozero' }},
      margin: {{ t: 10, r: 10, b: 80, l: 50 }}
    }});

    // Total per Status
    const byStatus = countBy(rows, 'status');
    const statusCats = {STATUS_ORDER}.filter(s => byStatus.has(s)).concat(
      Array.from(byStatus.keys()).filter(s => !{STATUS_ORDER}.includes(s))
    );
    const statusVals = statusCats.map(s => byStatus.get(s) || 0);

    Plotly.react('chart_status_total_plot', [{{
      type: 'bar',
      x: statusCats,
      y: statusVals
    }}], {{
      xaxis: {{ tickangle: -30 }},
      yaxis: {{ title: 'Trials', rangemode: 'tozero' }},
      margin: {{ t: 10, r: 10, b: 80, l: 50 }}
    }});

    // Intervention types (Top-N)
    const topI = Number(document.getElementById('topInterventions').value || {TOP_INTERVENTIONS});
    const byInterv = countInterventionTypes(rows);
    const sortedInterv = Array.from(byInterv.entries()).sort((a,b)=> b[1]-a[1]).slice(0, Math.max(1, topI));
    Plotly.react('chart_intervention_total_plot', [{{
      type: 'bar',
      x: sortedInterv.map(d=>d[0]),
      y: sortedInterv.map(d=>d[1])
    }}], {{
      xaxis: {{ tickangle: -30 }},
      yaxis: {{ title: 'Trials', rangemode: 'tozero' }},
      margin: {{ t: 10, r: 10, b: 100, l: 50 }}
    }});
  }}

  // Sponsor click-to-filter
  document.querySelector('#t tbody').addEventListener('click', function(e) {{
    const sponsorIdx = getColumnIndex('lead_sponsor');
    if (!e.target || !e.target.closest('td')) return;
    const cell = e.target.closest('td');
    const idx = dt.cell(cell).index().column;
    if (idx !== sponsorIdx) return;
    const sponsor = clean(cell.textContent);
    if (!sponsor) return;
    // exact match search on sponsor column
    const escaped = sponsor.replace(/[.*+?^${{}}()|[\\]\\\\]/g, '\\\\$&');
    dt.column(sponsorIdx).search('^' + escaped + '$', true, false).draw();
  }});

  // Reset filters button
  document.getElementById('resetFiltersBtn').addEventListener('click', () => {{
    dt.search('');
    dt.columns().every(function() {{ this.search(''); }});
    dt.draw();
  }});

  // Top-N inputs handlers
  document.getElementById('topSponsors').addEventListener('change', updateCharts);
  document.getElementById('topInterventions').addEventListener('change', updateCharts);

  // Update charts whenever table changes
  dt.on('draw', updateCharts);
  dt.on('search', updateCharts);
  dt.on('column-reorder', updateCharts);
  dt.on('column-visibility', updateCharts);

  // Initial render
  updateCharts();
</script>
</body>
</html>
"""

Path(html_path).write_text(html, encoding="utf-8")
print("Wrote:", csv_path)
print("Wrote:", html_path)
