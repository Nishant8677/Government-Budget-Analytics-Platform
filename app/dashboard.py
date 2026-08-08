"""
app/dashboard.py
────────────────
Government Budget Analytics Platform — Streamlit Dashboard

Six analytical tabs:
  1. 🏠 Overview        — KPI cards, national revenue trend, scheme composition
  2. 📈 Scheme Analysis — Top-N bar chart, year-over-year grouped comparison
  3. ⚖️  Budget vs Actuals — Scatter + utilisation bar
  4. 💡 Insights        — Auto-generated budget intelligence cards
  5. 🗄️  Query Console  — Pre-built domain SQL queries with export
  6. 🔍 Data Explorer   — Searchable / sortable table, CSV + Excel export

Performance:
  • @st.cache_resource — one connection pool per process, shared by all sessions
  • @st.cache_data(ttl=300) — 5-minute query result cache

  st.cache_resource caches globally, not per session. An earlier version of this
  docstring said "one DB connection per browser session" and held a bare
  connection at that scope, which meant every concurrent user shared one
  non-thread-safe connection. It caches the pool now; each query checks out its
  own connection and returns it on completion.

Run with:
    streamlit run app/dashboard.py        (from project root)
    python manage.py dashboard            (via CLI)
"""
from __future__ import annotations

import sys
from io import BytesIO
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st
from mysql.connector import Error

from utils.db import get_pool
from utils.logger import get_logger

logger = get_logger(__name__)

APP_NAME = "Government Budget Analytics Platform"
APP_SHORT = "GBAP"

# ─────────────────────────────────────────────────────────────────────────────
# Page config
# ─────────────────────────────────────────────────────────────────────────────
st.set_page_config(
    page_title=f"{APP_NAME}",
    page_icon="🏛️",
    layout="wide",
    initial_sidebar_state="expanded",
    menu_items={"About": f"{APP_NAME} | Indian Union Budget Tax Revenue Analytics"},
)

# ─────────────────────────────────────────────────────────────────────────────
# CSS
# ─────────────────────────────────────────────────────────────────────────────
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&display=swap');
html, body, [class*="css"] { font-family: 'Inter', sans-serif; }

.stApp { background: linear-gradient(135deg, #0f0c29 0%, #302b63 50%, #24243e 100%); }
#MainMenu {visibility:hidden;} footer {visibility:hidden;} header {visibility:hidden;}

.hero-header {
    background: linear-gradient(90deg,#1a1a6e 0%,#b21f1f 50%,#1a1a6e 100%);
    padding:2rem 2.5rem; border-radius:16px; margin-bottom:1.5rem;
    box-shadow:0 8px 32px rgba(178,31,31,0.4); position:relative; overflow:hidden;
}
.hero-header::before {
    content:''; position:absolute; top:-50%; right:-10%;
    width:300px; height:300px;
    background:rgba(255,255,255,0.05); border-radius:50%;
}
.hero-title  { font-size:1.9rem; font-weight:700; color:#fff; margin:0; letter-spacing:-0.5px; }
.hero-sub    { font-size:0.95rem; color:rgba(255,255,255,0.8); margin:0.3rem 0 0; }
.hero-badge  {
    display:inline-block; background:rgba(255,255,255,0.15);
    color:#fff; padding:0.2rem 0.8rem; border-radius:20px;
    font-size:0.72rem; font-weight:500; margin-top:0.7rem;
}

.kpi-card {
    background:rgba(255,255,255,0.07); backdrop-filter:blur(10px);
    border:1px solid rgba(255,255,255,0.12); border-radius:14px;
    padding:1.4rem 1.2rem; text-align:center;
    transition:transform 0.2s ease,box-shadow 0.2s ease; margin-bottom:1rem;
}
.kpi-card:hover { transform:translateY(-3px); box-shadow:0 12px 40px rgba(102,126,234,0.3); }
.kpi-icon  { font-size:2rem; margin-bottom:0.4rem; display:block; }
.kpi-value { font-size:1.75rem; font-weight:700; color:#fff; line-height:1; }
.kpi-label { font-size:0.76rem; color:rgba(255,255,255,0.55); font-weight:500;
             letter-spacing:0.5px; text-transform:uppercase; margin-top:0.4rem; }
.kpi-delta { font-size:0.8rem; font-weight:600; margin-top:0.5rem; }
.kpi-delta.positive { color:#4ade80; }
.kpi-delta.negative { color:#f87171; }
.kpi-delta.neutral  { color:rgba(255,255,255,0.5); }

.insight-card {
    background:rgba(255,255,255,0.06); border:1px solid rgba(255,255,255,0.1);
    border-radius:12px; padding:1.2rem 1.4rem; margin-bottom:0.8rem;
}
.insight-title { font-size:0.72rem; color:rgba(255,255,255,0.5);
                 text-transform:uppercase; letter-spacing:0.6px; }
.insight-value { font-size:1.3rem; font-weight:700; color:#fff; margin:0.25rem 0 0; }
.insight-detail{ font-size:0.82rem; color:rgba(255,255,255,0.6); margin-top:0.2rem; }

.section-header {
    font-size:1.1rem; font-weight:600; color:#e2e8f0;
    padding:0.5rem 0; border-bottom:2px solid rgba(102,126,234,0.5);
    margin:1.5rem 0 1rem;
}
.stTabs [data-baseweb="tab-list"] {
    background:rgba(255,255,255,0.05); border-radius:10px; padding:4px; gap:4px;
}
.stTabs [data-baseweb="tab"] { border-radius:8px; color:rgba(255,255,255,0.6); font-weight:500; }
.stTabs [aria-selected="true"] {
    background:linear-gradient(90deg,#1a1a6e,#b21f1f) !important; color:white !important;
}
.query-card {
    background:rgba(255,255,255,0.05); border:1px solid rgba(255,255,255,0.1);
    border-radius:10px; padding:1rem 1.2rem; margin-bottom:0.6rem; cursor:pointer;
}
</style>
""", unsafe_allow_html=True)

PLOTLY_TEMPLATE = "plotly_dark"
PALETTE = ["#e63946", "#1d3557", "#457b9d", "#a8dadc", "#f4a261", "#2a9d8f"]

# ─────────────────────────────────────────────────────────────────────────────
# Saved domain queries for Query Console
# ─────────────────────────────────────────────────────────────────────────────
SAVED_QUERIES: dict[str, dict] = {
    "🏆 Top 10 Revenue Sources (2020-21 Actuals)": {
        "description": "Highest actual collections by sub-scheme in FY 2020-21",
        "sql": """
SELECT   ss.sub_scheme_name,
         s.scheme_name,
         mh.major_head_code,
         bd.actuals AS actuals_crore
FROM     budget_data   bd
JOIN     sub_schemes   ss ON bd.sub_scheme_id  = ss.sub_scheme_id
JOIN     schemes        s ON ss.scheme_id       = s.scheme_id
LEFT JOIN major_heads  mh ON ss.major_head_id   = mh.major_head_id
JOIN     fiscal_years  fy ON bd.fiscal_year_id  = fy.fiscal_year_id
WHERE    fy.fiscal_year = '2020-2021'
  AND    bd.actuals > 0
ORDER BY bd.actuals DESC
LIMIT    10;
""",
    },
    "📊 Budget Estimates: 2021-22 vs 2022-23 by Scheme": {
        "description": "Compare budget allocations across consecutive fiscal years",
        "sql": """
SELECT
    s.scheme_name,
    MAX(CASE WHEN fy.fiscal_year = '2021-2022' THEN total_budget END) AS budget_2021_22,
    MAX(CASE WHEN fy.fiscal_year = '2022-2023' THEN total_budget END) AS budget_2022_23,
    ROUND(
        MAX(CASE WHEN fy.fiscal_year = '2022-2023' THEN total_budget END) -
        MAX(CASE WHEN fy.fiscal_year = '2021-2022' THEN total_budget END),
        2
    ) AS budget_change_crore
FROM     v_scheme_summary ss2
JOIN     schemes            s  ON ss2.scheme_name = s.scheme_name
JOIN     fiscal_years      fy  ON ss2.fiscal_year = fy.fiscal_year
WHERE    ss2.fiscal_year IN ('2021-2022','2022-2023')
GROUP BY s.scheme_name
HAVING   budget_2021_22 IS NOT NULL
ORDER BY budget_change_crore DESC;
""",
    },
    "⚖️ Revised vs Budget Deviation (2021-22)": {
        "description": "Schemes where revised estimate diverged most from original budget",
        "sql": """
SELECT   scheme_name,
         ROUND(total_budget,  2) AS original_budget,
         ROUND(total_revised, 2) AS revised_estimate,
         ROUND(total_revised - total_budget, 2) AS deviation_crore,
         ROUND(((total_revised - total_budget) / total_budget) * 100, 2) AS deviation_pct
FROM     v_scheme_summary
WHERE    fiscal_year  = '2021-2022'
  AND    total_budget  IS NOT NULL
  AND    total_revised IS NOT NULL
  AND    total_budget  > 0
ORDER BY ABS(deviation_pct) DESC;
""",
    },
    "🔢 Revenue by Major Head Code (2020-21)": {
        "description": "Total actual collections grouped by government accounting head",
        "sql": """
SELECT   mh.major_head_code,
         ROUND(SUM(bd.actuals), 2) AS total_actuals_crore,
         COUNT(DISTINCT ss.sub_scheme_id) AS sub_schemes_count
FROM     budget_data   bd
JOIN     sub_schemes   ss ON bd.sub_scheme_id = ss.sub_scheme_id
JOIN     major_heads   mh ON ss.major_head_id = mh.major_head_id
JOIN     fiscal_years  fy ON bd.fiscal_year_id = fy.fiscal_year_id
WHERE    fy.fiscal_year = '2020-2021'
  AND    bd.actuals IS NOT NULL
GROUP BY mh.major_head_code
ORDER BY total_actuals_crore DESC;
""",
    },
    "📋 Full Budget Overview": {
        "description": "Complete denormalised view — all sub-schemes, all fiscal years",
        "sql": """
SELECT   group_name, scheme_name, sub_scheme_name,
         major_head_code, fiscal_year,
         actuals, budget, revised,
         budget_utilization_pct
FROM     v_budget_overview
ORDER BY group_name, scheme_name, sub_scheme_name, fiscal_year;
""",
    },
}

# ─────────────────────────────────────────────────────────────────────────────
# DB connection (cached at session level)
# ─────────────────────────────────────────────────────────────────────────────
@st.cache_resource(show_spinner=False)
def _get_pool():
    """One pool per process, shared by every session.

    st.cache_resource is global across sessions and users -- not per-session, as
    an earlier version of this module claimed. A pool is the right thing to hold
    at that scope; a bare connection is not, because mysql-connector connections
    are not thread-safe and Streamlit gives each session its own thread.
    """
    return get_pool()


def _run_query(sql: str, params: tuple = ()) -> pd.DataFrame:
    conn = _get_pool().get_connection()
    try:
        # Pooled connections outlive MySQL's wait_timeout, so a dashboard left
        # open overnight can be handed a dead socket. Ping reconnects instead of
        # failing the query.
        conn.ping(reconnect=True, attempts=2, delay=1)
        return pd.read_sql(sql, conn, params=params)
    except Error as exc:
        logger.error("Query failed: %s", exc)
        raise
    finally:
        conn.close()  # returns it to the pool rather than closing the socket


# ─────────────────────────────────────────────────────────────────────────────
# Data loaders (cached with 5-minute TTL)
# ─────────────────────────────────────────────────────────────────────────────
@st.cache_data(ttl=300, show_spinner=False)
def load_fiscal_years() -> list[str]:
    df = _run_query("SELECT fiscal_year FROM fiscal_years ORDER BY fiscal_year")
    return df["fiscal_year"].tolist()


@st.cache_data(ttl=300, show_spinner=False)
def load_schemes() -> list[str]:
    df = _run_query("SELECT scheme_name FROM schemes ORDER BY scheme_name")
    return df["scheme_name"].tolist()


@st.cache_data(ttl=300, show_spinner=False)
def load_fiscal_year_totals() -> pd.DataFrame:
    return _run_query("SELECT * FROM v_fiscal_year_totals ORDER BY fiscal_year")


@st.cache_data(ttl=300, show_spinner=False)
def load_scheme_summary(fiscal_year: str) -> pd.DataFrame:
    if fiscal_year != "All Years":
        return _run_query(
            "SELECT * FROM v_scheme_summary WHERE fiscal_year = %s ORDER BY total_actuals DESC",
            (fiscal_year,),
        )
    # Deliberately bypasses v_scheme_summary. Filtered to one year the view
    # costs ~1 ms, because MySQL pushes the predicate into it. Unfiltered there
    # is nothing to push, so it groups by (scheme, group, fiscal_year) and
    # computes COUNT(DISTINCT sub_scheme_id) for every group before this outer
    # query throws the per-year split away again. Measured at 921,696 rows that
    # is 205 s against 23.0 s for the aggregation below, which produces
    # byte-identical rows. See PERFORMANCE.md.
    return _run_query("""
        SELECT   s.scheme_name, g.group_name,
                 ROUND(SUM(bd.actuals), 2) AS total_actuals,
                 ROUND(SUM(bd.budget),  2) AS total_budget,
                 ROUND(SUM(bd.revised), 2) AS total_revised
        FROM     budget_data bd
        JOIN     sub_schemes ss ON bd.sub_scheme_id = ss.sub_scheme_id
        JOIN     schemes      s ON ss.scheme_id     = s.scheme_id
        JOIN     `groups`     g ON s.group_id       = g.group_id
        GROUP BY s.scheme_name, g.group_name
        ORDER BY total_actuals DESC
    """)


@st.cache_data(ttl=300, show_spinner=False)
def load_budget_overview(fiscal_year: str, scheme: str) -> pd.DataFrame:
    conditions, params = [], []
    if fiscal_year != "All Years":
        conditions.append("fiscal_year = %s")
        params.append(fiscal_year)
    if scheme != "All Schemes":
        conditions.append("scheme_name = %s")
        params.append(scheme)
    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    return _run_query(
        # programme_name is selected because it is part of a sub-scheme's
        # identity: several levies share a sub-scheme name and are only
        # distinguishable by it. Without it the table shows repeated rows with
        # different figures and no way to tell them apart.
        f"""SELECT group_name, scheme_name, sub_scheme_name, programme_name,
                   sub_programme_name, major_head_code,
                   fiscal_year, actuals, budget, revised, budget_utilization_pct
            FROM   v_budget_overview {where}
            ORDER BY group_name, scheme_name, sub_scheme_name,
                     programme_name, fiscal_year""",
        tuple(params),
    )


@st.cache_data(ttl=300, show_spinner=False)
def load_insights() -> dict:
    """Compute auto-generated budget intelligence metrics."""
    out: dict = {}

    # Top scheme by actuals (2020-2021)
    df = _run_query("""
        SELECT scheme_name, ROUND(SUM(total_actuals),2) AS total_actuals
        FROM   v_scheme_summary WHERE fiscal_year='2020-2021'
        GROUP BY scheme_name ORDER BY total_actuals DESC LIMIT 1
    """)
    out["top_scheme"] = df.iloc[0].to_dict() if not df.empty else {}

    # Highest budget growth 2021→2022 vs 2022→2023
    # The two years are UNIONed as separate equality filters rather than
    # selected with `fiscal_year IN (...)`. MySQL pushes a single equality
    # predicate down into v_scheme_summary's GROUP BY, but not a disjunction --
    # IN and OR both measured 874 ms because neither pushes, leaving the view to
    # aggregate all 921,696 rows. Filtered one year at a time each branch pushes
    # down, and the same rows come back in 2.37 ms. See PERFORMANCE.md.
    df = _run_query("""
        SELECT scheme_name,
               MAX(CASE WHEN fiscal_year='2021-2022' THEN total_budget END) AS b21,
               MAX(CASE WHEN fiscal_year='2022-2023' THEN total_budget END) AS b22
        FROM ( SELECT * FROM v_scheme_summary WHERE fiscal_year='2021-2022'
               UNION ALL
               SELECT * FROM v_scheme_summary WHERE fiscal_year='2022-2023' ) y
        GROUP  BY scheme_name HAVING b21>0 AND b22>0
        ORDER  BY (b22-b21)/b21 DESC LIMIT 1
    """)
    out["highest_growth"] = df.iloc[0].to_dict() if not df.empty else {}

    # Largest budget revision (revised vs budget 2021-22)
    df = _run_query("""
        SELECT scheme_name,
               ROUND(total_budget,2) AS budget,
               ROUND(total_revised,2) AS revised,
               ROUND(ABS(total_revised-total_budget),2) AS deviation
        FROM   v_scheme_summary
        WHERE  fiscal_year='2021-2022' AND total_budget>0 AND total_revised IS NOT NULL
        ORDER  BY deviation DESC LIMIT 1
    """)
    out["largest_revision"] = df.iloc[0].to_dict() if not df.empty else {}

    # Average budget utilisation (schemes with both actuals 2020-21 budget)
    # Note: 2020-21 only has actuals; use revised_2021-22 vs budget_2021-22
    df = _run_query("""
        SELECT ROUND(AVG((total_revised/total_budget)*100),2) AS avg_util
        FROM   v_scheme_summary
        WHERE  fiscal_year='2021-2022' AND total_budget>0 AND total_revised IS NOT NULL
    """)
    out["avg_util"] = float(df.iloc[0]["avg_util"]) if not df.empty else 0.0

    # Total schemes with actuals > budget (2021-22 revised > budget)
    df = _run_query("""
        SELECT COUNT(*) AS over_count
        FROM   v_scheme_summary
        WHERE  fiscal_year='2021-2022' AND total_revised > total_budget
    """)
    out["schemes_over_budget"] = int(df.iloc[0]["over_count"]) if not df.empty else 0

    return out


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────
def _fmt_crore(v) -> str:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return "N/A"
    v = float(v)
    if abs(v) >= 1_00_000:
        return f"₹{v/1_00_000:.2f}L Cr"
    if abs(v) >= 1_000:
        return f"₹{v/1_000:.1f}K Cr"
    return f"₹{v:.1f} Cr"


def _to_excel(df: pd.DataFrame) -> bytes:
    buf = BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="BudgetData")
    return buf.getvalue()


def _check_connection() -> bool:
    """Health probe: can we take a connection from the pool and use it?

    Checks out and returns one rather than inspecting a cached handle, so a
    healthy result means a query would actually succeed right now.
    """
    try:
        conn = _get_pool().get_connection()
        try:
            conn.ping(reconnect=True, attempts=1, delay=0)
            return conn.is_connected()
        finally:
            conn.close()
    except Exception:
        return False


# ─────────────────────────────────────────────────────────────────────────────
# Hero header
# ─────────────────────────────────────────────────────────────────────────────
st.markdown(f"""
<div class="hero-header">
    <p class="hero-title">🏛️ {APP_NAME}</p>
    <p class="hero-sub">Indian Union Budget · Tax Revenue Analytics · FY 2020–2023</p>
    <span class="hero-badge">🇮🇳 MySQL · Streamlit · Plotly · Python</span>
</div>
""", unsafe_allow_html=True)

# ─────────────────────────────────────────────────────────────────────────────
# Connection guard
# ─────────────────────────────────────────────────────────────────────────────
if not _check_connection():
    st.error(
        "**Database not connected.**  \n"
        "1. Copy `.env.example` → `.env` and fill in your MySQL credentials.  \n"
        "2. `python manage.py setup`  \n"
        "3. `python manage.py load`  \n"
        "4. `python manage.py dashboard`"
    )
    st.stop()

# ─────────────────────────────────────────────────────────────────────────────
# Sidebar filters
# ─────────────────────────────────────────────────────────────────────────────
with st.sidebar:
    st.markdown("### 🎛️ Filters")
    st.markdown("---")
    fiscal_years = load_fiscal_years()
    selected_fy = st.selectbox("📅 Fiscal Year", ["All Years"] + fiscal_years)
    all_schemes = load_schemes()
    selected_scheme = st.selectbox("🏷️ Scheme", ["All Schemes"] + all_schemes)
    st.markdown("---")
    st.markdown("""
<div style="font-size:0.76rem;color:rgba(255,255,255,0.35);line-height:1.7">
    <b>Source</b>: Government of India<br>
    Union Budget 2022-23<br>
    Statement 14: Tax Revenue<br><br>
    <b>Unit</b>: ₹ Crore
</div>""", unsafe_allow_html=True)

# ─────────────────────────────────────────────────────────────────────────────
# Load data
# ─────────────────────────────────────────────────────────────────────────────
fy_totals      = load_fiscal_year_totals()
scheme_summary = load_scheme_summary(selected_fy)
detail_df      = load_budget_overview(selected_fy, selected_scheme)
insights       = load_insights()

# ─────────────────────────────────────────────────────────────────────────────
# Tabs
# ─────────────────────────────────────────────────────────────────────────────
tab_ov, tab_sc, tab_ba, tab_ins, tab_qc, tab_ex = st.tabs([
    "🏠 Overview", "📈 Scheme Analysis", "⚖️ Budget vs Actuals",
    "💡 Insights", "🗄️ Query Console", "🔍 Data Explorer",
])

# ══════════════════════════════════════════════════════════════════════════════
# TAB 1 — OVERVIEW
# ══════════════════════════════════════════════════════════════════════════════
with tab_ov:
    latest = fy_totals.iloc[-1] if not fy_totals.empty else None
    prev   = fy_totals.iloc[-2] if len(fy_totals) > 1 else None

    c1, c2, c3, c4 = st.columns(4)
    with c1:
        v = float(latest["total_actuals"]) if latest is not None else 0
        d, css = ("—", "neutral") if prev is None else (
            ("+" if v >= float(prev["total_actuals"]) else ""),
            "positive" if v >= float(prev["total_actuals"]) else "negative"
        )
        st.markdown(f"""<div class="kpi-card"><span class="kpi-icon">💰</span>
            <div class="kpi-value">{_fmt_crore(v)}</div>
            <div class="kpi-label">Actual Collections (2020-21)</div>
            <div class="kpi-delta {css}">Highest available year</div></div>""",
            unsafe_allow_html=True)
    with c2:
        bdf = fy_totals[fy_totals["total_budget"].notna()]
        bv  = float(bdf.iloc[-1]["total_budget"]) if not bdf.empty else 0
        st.markdown(f"""<div class="kpi-card"><span class="kpi-icon">📋</span>
            <div class="kpi-value">{_fmt_crore(bv)}</div>
            <div class="kpi-label">Budget Estimate (2022-23)</div>
            <div class="kpi-delta neutral">Latest Projection</div></div>""",
            unsafe_allow_html=True)
    with c3:
        st.markdown(f"""<div class="kpi-card"><span class="kpi-icon">🗂️</span>
            <div class="kpi-value">{len(all_schemes)}</div>
            <div class="kpi-label">Schemes Tracked</div>
            <div class="kpi-delta neutral">Across 3 Fiscal Years</div></div>""",
            unsafe_allow_html=True)
    with c4:
        top = insights.get("top_scheme", {})
        st.markdown(f"""<div class="kpi-card"><span class="kpi-icon">🏆</span>
            <div class="kpi-value" style="font-size:1rem">{top.get('scheme_name','—')}</div>
            <div class="kpi-label">Highest Revenue Scheme</div>
            <div class="kpi-delta positive">{_fmt_crore(top.get('total_actuals'))}</div></div>""",
            unsafe_allow_html=True)

    st.markdown("---")
    st.markdown('<div class="section-header">📉 National Tax Revenue Trend</div>', unsafe_allow_html=True)
    if not fy_totals.empty:
        trend = fy_totals.melt(
            "fiscal_year", ["total_actuals", "total_budget", "total_revised"], "Metric", "₹ Crore"
        ).dropna()
        trend["Metric"] = trend["Metric"].map(
            {"total_actuals": "Actuals", "total_budget": "Budget", "total_revised": "Revised"}
        )
        fig = px.line(trend, x="fiscal_year", y="₹ Crore", color="Metric", markers=True,
                      title="Annual Tax Revenue: Actuals vs Budget vs Revised (₹ Crore)",
                      color_discrete_sequence=PALETTE, template=PLOTLY_TEMPLATE)
        fig.update_layout(paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                          legend=dict(orientation="h", yanchor="bottom", y=1.02))
        fig.update_traces(line_width=2.5, marker_size=8)
        st.plotly_chart(fig, use_container_width=True)

    st.markdown('<div class="section-header">🥧 Revenue Composition by Scheme</div>', unsafe_allow_html=True)
    pie_df = scheme_summary.dropna(subset=["total_actuals"])
    if not pie_df.empty:
        fig2 = px.pie(pie_df.head(10), names="scheme_name", values="total_actuals",
                      title=f"Revenue Share ({selected_fy})", hole=0.4,
                      color_discrete_sequence=PALETTE, template=PLOTLY_TEMPLATE)
        fig2.update_layout(paper_bgcolor="rgba(0,0,0,0)")
        st.plotly_chart(fig2, use_container_width=True)

# ══════════════════════════════════════════════════════════════════════════════
# TAB 2 — SCHEME ANALYSIS
# ══════════════════════════════════════════════════════════════════════════════
with tab_sc:
    st.markdown('<div class="section-header">📊 Scheme-wise Revenue (₹ Crore)</div>', unsafe_allow_html=True)
    if not scheme_summary.empty:
        top_n = st.slider("Show top N schemes", 3, min(len(scheme_summary), 15), 8)
        plot_df = scheme_summary.head(top_n).dropna(subset=["total_actuals"])
        fig = px.bar(plot_df, x="total_actuals", y="scheme_name", orientation="h",
                     title=f"Top {top_n} Schemes by Actual Collections ({selected_fy})",
                     color="total_actuals", color_continuous_scale="Viridis",
                     template=PLOTLY_TEMPLATE, labels={"total_actuals":"₹ Crore","scheme_name":"Scheme"})
        fig.update_layout(paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                          yaxis=dict(autorange="reversed"), coloraxis_showscale=False)
        st.plotly_chart(fig, use_container_width=True)

        st.markdown('<div class="section-header">📆 Year-over-Year Budget Comparison</div>', unsafe_allow_html=True)
        top_names = plot_df["scheme_name"].tolist()
        yoy_df = _run_query(
            "SELECT scheme_name, fiscal_year, COALESCE(total_actuals,0) AS total_actuals "
            "FROM v_scheme_summary WHERE scheme_name IN ({}) ORDER BY fiscal_year".format(
                ",".join(["%s"]*len(top_names))),
            tuple(top_names),
        )
        if not yoy_df.empty:
            fig2 = px.bar(yoy_df, x="scheme_name", y="total_actuals", color="fiscal_year",
                          barmode="group", title="Year-over-Year Revenue by Scheme (₹ Crore)",
                          color_discrete_sequence=PALETTE, template=PLOTLY_TEMPLATE,
                          labels={"total_actuals":"₹ Crore","scheme_name":"Scheme"})
            fig2.update_layout(paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                               xaxis_tickangle=-35)
            st.plotly_chart(fig2, use_container_width=True)

# ══════════════════════════════════════════════════════════════════════════════
# TAB 3 — BUDGET VS ACTUALS
# ══════════════════════════════════════════════════════════════════════════════
with tab_ba:
    st.markdown('<div class="section-header">⚖️ Budget Utilisation Analysis</div>', unsafe_allow_html=True)
    util = scheme_summary.dropna(subset=["total_actuals","total_budget"]).copy()
    util = util[util["total_budget"] > 0].copy()
    util["util_pct"] = (util["total_actuals"] / util["total_budget"] * 100).round(2)
    if not util.empty:
        ca, cb = st.columns(2)
        with ca:
            st.metric("Schemes Over-performing", len(util[util["util_pct"] > 100]))
        with cb:
            st.metric("Schemes Under-performing (<80%)", len(util[util["util_pct"] < 80]))
        mx = max(util["total_budget"].max(), util["total_actuals"].max())
        fig = px.scatter(util, x="total_budget", y="total_actuals", size="util_pct",
                         color="util_pct", hover_name="scheme_name",
                         color_continuous_scale="RdYlGn", template=PLOTLY_TEMPLATE,
                         title="Budget Estimate vs Actual Collections (bubble = utilisation %)",
                         labels={"total_budget":"Budget (₹ Cr)","total_actuals":"Actuals (₹ Cr)","util_pct":"Util %"})
        fig.add_trace(go.Scatter(x=[0,mx], y=[0,mx], mode="lines",
                                 line=dict(color="rgba(255,255,255,0.3)", dash="dash"),
                                 name="100% Utilisation"))
        fig.update_layout(paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)")
        st.plotly_chart(fig, use_container_width=True)
        fig2 = px.bar(util.sort_values("util_pct", ascending=False), x="scheme_name", y="util_pct",
                      color="util_pct", color_continuous_scale="RdYlGn", template=PLOTLY_TEMPLATE,
                      title="Budget Utilisation by Scheme (%)",
                      labels={"util_pct":"Utilisation %","scheme_name":"Scheme"})
        fig2.add_hline(y=100, line_dash="dash", line_color="rgba(255,255,255,0.4)", annotation_text="100% Target")
        fig2.update_layout(paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                           xaxis_tickangle=-35, coloraxis_showscale=False)
        st.plotly_chart(fig2, use_container_width=True)
    else:
        st.info("No budget utilisation data for the current filter selection.")

# ══════════════════════════════════════════════════════════════════════════════
# TAB 4 — INSIGHTS
# ══════════════════════════════════════════════════════════════════════════════
with tab_ins:
    st.markdown('<div class="section-header">💡 Auto-Generated Budget Intelligence</div>', unsafe_allow_html=True)
    st.caption("Insights are computed directly from the database — no manual curation.")

    c1, c2, c3 = st.columns(3)
    with c1:
        top = insights.get("top_scheme", {})
        st.markdown(f"""<div class="insight-card">
            <div class="insight-title">🏆 Highest Revenue Scheme (2020-21)</div>
            <div class="insight-value">{top.get('scheme_name','—')}</div>
            <div class="insight-detail">Actual collections: {_fmt_crore(top.get('total_actuals'))}</div>
        </div>""", unsafe_allow_html=True)

    with c2:
        rev = insights.get("largest_revision", {})
        if rev:
            dev = float(rev.get("deviation", 0))
            direction = "above" if float(rev.get("revised",0)) > float(rev.get("budget",0)) else "below"
            st.markdown(f"""<div class="insight-card">
                <div class="insight-title">🔄 Largest Budget Revision (2021-22)</div>
                <div class="insight-value">{rev.get('scheme_name','—')}</div>
                <div class="insight-detail">Revised {direction} budget by {_fmt_crore(dev)}</div>
            </div>""", unsafe_allow_html=True)

    with c3:
        gr = insights.get("highest_growth", {})
        if gr:
            b21, b22 = float(gr.get("b21",0)), float(gr.get("b22",0))
            pct = ((b22-b21)/b21*100) if b21 > 0 else 0
            st.markdown(f"""<div class="insight-card">
                <div class="insight-title">📈 Highest Budget Growth (21-22 → 22-23)</div>
                <div class="insight-value">{gr.get('scheme_name','—')}</div>
                <div class="insight-detail">+{pct:.1f}% budget increase YoY</div>
            </div>""", unsafe_allow_html=True)

    c4, c5 = st.columns(2)
    with c4:
        avg = insights.get("avg_util", 0)
        colour = "#4ade80" if avg >= 90 else "#fb923c" if avg >= 70 else "#f87171"
        st.markdown(f"""<div class="insight-card">
            <div class="insight-title">⚖️ Avg Revised/Budget Utilisation (2021-22)</div>
            <div class="insight-value" style="color:{colour}">{avg:.1f}%</div>
            <div class="insight-detail">Average across all schemes with both figures</div>
        </div>""", unsafe_allow_html=True)

    with c5:
        ob = insights.get("schemes_over_budget", 0)
        st.markdown(f"""<div class="insight-card">
            <div class="insight-title">🚨 Schemes Exceeding Budget (2021-22)</div>
            <div class="insight-value">{ob}</div>
            <div class="insight-detail">Schemes where revised estimate > original budget</div>
        </div>""", unsafe_allow_html=True)

    # ── Budget growth waterfall ────────────────────────────────────────────
    st.markdown('<div class="section-header">📊 Budget Growth: 2021-22 → 2022-23</div>', unsafe_allow_html=True)
    growth_df = _run_query("""
        SELECT scheme_name,
               MAX(CASE WHEN fiscal_year='2021-2022' THEN total_budget END) AS b21,
               MAX(CASE WHEN fiscal_year='2022-2023' THEN total_budget END) AS b22
        FROM   v_scheme_summary WHERE fiscal_year IN ('2021-2022','2022-2023')
        GROUP  BY scheme_name HAVING b21>0 AND b22>0
        ORDER  BY (b22-b21) DESC LIMIT 12
    """)
    if not growth_df.empty:
        growth_df["change"] = growth_df["b22"] - growth_df["b21"]
        growth_df["pct"]    = ((growth_df["b22"]-growth_df["b21"])/growth_df["b21"]*100).round(2)
        fig = px.bar(growth_df, x="scheme_name", y="change",
                     color="pct", color_continuous_scale="RdYlGn",
                     title="Absolute Budget Change by Scheme (₹ Crore)",
                     template=PLOTLY_TEMPLATE,
                     labels={"change":"Change (₹ Cr)","scheme_name":"Scheme","pct":"% Change"})
        fig.update_layout(paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                          xaxis_tickangle=-35)
        st.plotly_chart(fig, use_container_width=True)

# ══════════════════════════════════════════════════════════════════════════════
# TAB 5 — QUERY CONSOLE
# ══════════════════════════════════════════════════════════════════════════════
with tab_qc:
    st.markdown('<div class="section-header">🗄️ Pre-Built Domain Queries</div>', unsafe_allow_html=True)
    st.caption("Select a query, review the SQL, run it, and export the results.")

    selected_qname = st.selectbox(
        "Choose a query",
        list(SAVED_QUERIES.keys()),
        index=0,
    )
    qinfo = SAVED_QUERIES[selected_qname]
    st.info(qinfo["description"])

    with st.expander("📝 View SQL", expanded=False):
        st.code(qinfo["sql"].strip(), language="sql")

    if st.button("▶️ Run Query", type="primary"):
        with st.spinner("Executing …"):
            try:
                result_df = _run_query(qinfo["sql"])
                st.success(f"✅ {len(result_df):,} rows returned")
                st.dataframe(result_df, use_container_width=True, height=380)

                c1, c2 = st.columns(2)
                with c1:
                    st.download_button(
                        "⬇️ Download CSV",
                        result_df.to_csv(index=False),
                        "query_result.csv", "text/csv",
                    )
                with c2:
                    try:
                        st.download_button(
                            "📊 Download Excel",
                            _to_excel(result_df),
                            "query_result.xlsx",
                            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        )
                    except Exception:
                        st.caption("Excel export requires: pip install openpyxl")
            except Exception as e:
                st.error(f"Query failed: {e}")

    st.markdown("---")
    st.markdown("**All 5 pre-built queries:**")
    for name, info in SAVED_QUERIES.items():
        st.markdown(f"- **{name}** — {info['description']}")

# ══════════════════════════════════════════════════════════════════════════════
# TAB 6 — DATA EXPLORER
# ══════════════════════════════════════════════════════════════════════════════
with tab_ex:
    st.markdown('<div class="section-header">🔍 Interactive Data Explorer</div>', unsafe_allow_html=True)
    if not detail_df.empty:
        st.caption(f"**{len(detail_df):,}** records | FY: **{selected_fy}** | Scheme: **{selected_scheme}**")
        # regex=False on the filters below: pandas defaults str.contains to
        # regex, so a user typing "(" raised re.error and a pathological
        # pattern was a ReDoS against the app process. This is a search box,
        # not a regex console.
        search = st.text_input("🔎 Search", placeholder="scheme, sub-scheme or programme …")
        disp = detail_df.copy()
        if search:
            mask = (
                disp["sub_scheme_name"].str.contains(search, case=False, na=False, regex=False)
                | disp["programme_name"].str.contains(search, case=False, na=False, regex=False)
                | disp["scheme_name"].str.contains(search, case=False, na=False, regex=False)
            )
            disp = disp[mask]
        sort_col = st.selectbox("Sort by", ["actuals","budget","revised","budget_utilization_pct"])
        sort_asc = st.checkbox("Ascending", value=False)
        disp = disp.sort_values(sort_col, ascending=sort_asc, na_position="last").reset_index(drop=True)
        st.dataframe(disp, use_container_width=True, height=420)

        st.markdown("---")
        c1, c2 = st.columns(2)
        with c1:
            st.download_button("⬇️ Export CSV", disp.to_csv(index=False),
                               f"gbap_export_{selected_fy}_{selected_scheme}.csv", "text/csv")
        with c2:
            try:
                st.download_button("📊 Export Excel", _to_excel(disp),
                                   f"gbap_export_{selected_fy}_{selected_scheme}.xlsx",
                                   "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
            except Exception:
                st.caption("Excel export requires: pip install openpyxl")
    else:
        st.info("No data for the current filter selection.")

# ─────────────────────────────────────────────────────────────────────────────
# Footer
# ─────────────────────────────────────────────────────────────────────────────
st.markdown("---")
st.caption(f"🏛️ {APP_NAME} · Data: Government of India Union Budget 2022-23 · MySQL + Streamlit + Plotly")
