# -----------------------------
# app.py (drop-in full version)
# -----------------------------
# Force local imports (avoid stray data.py)
import os, sys, importlib.util
APP_DIR = os.path.dirname(os.path.abspath(__file__))
if APP_DIR not in sys.path:
    sys.path.insert(0, APP_DIR)

# Load local data.py explicitly
spec = importlib.util.spec_from_file_location("data_local", os.path.join(APP_DIR, "data.py"))
data_local = importlib.util.module_from_spec(spec)
spec.loader.exec_module(data_local)
load_all = data_local.load_all

# Standard imports
import io
import numpy as np
import pandas as pd
import streamlit as st
import plotly.graph_objects as go
from streamlit_option_menu import option_menu

from datetime import datetime

# Where your canonical Excel files live (same as data.py expects)
TXN_DISK_PATH = os.path.join(APP_DIR, "01.xlsx")
BDG_DISK_PATH = os.path.join(APP_DIR, "02_budget.xlsx")
BACKUP_DIR    = os.path.join(APP_DIR, "_backups")
os.makedirs(BACKUP_DIR, exist_ok=True)

def _norm_cols(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df.columns = [str(c).strip() for c in df.columns]
    return df

def validate_transactions(df: pd.DataFrame) -> list[str]:
    """Return a list of problems; empty list means OK."""
    issues = []
    req = {"Date", "AMOUNT", "REVENUE/EXPENSES"}
    cols = set(df.columns)
    missing = req - cols
    if missing:
        issues.append(f"Missing required columns: {sorted(list(missing))}")
        return issues

    # date check
    _date = pd.to_datetime(df["Date"], errors="coerce")
    bad_dates = _date.isna().sum()
    if bad_dates:
        issues.append(f"{bad_dates} rows have invalid 'Date' values.")

    # amount numeric/finite
    try:
        amt = pd.to_numeric(df["AMOUNT"], errors="coerce")
        if amt.isna().any():
            issues.append("Some 'AMOUNT' values are non-numeric.")
    except Exception:
        issues.append("Failed to coerce 'AMOUNT' to numeric.")

    # side values
    side_bad = ~df["REVENUE/EXPENSES"].astype(str).str.lower().isin(["revenue","expenses"])
    if side_bad.any():
        issues.append(f"{side_bad.sum()} rows have REVENUE/EXPENSES not in ['Revenue','Expenses'].")

    return issues

def validate_budget(df: pd.DataFrame) -> list[str]:
    issues = []
    df = _norm_cols(df)
    # we accept BUDGET or budget_amount; Date optional but recommended
    if "budget_amount" not in df.columns and "BUDGET" not in df.columns:
        issues.append("Budget must include 'BUDGET' or 'budget_amount' column.")
    if "Date" not in df.columns and "DATE" not in df.columns:
        issues.append("Budget should include a 'Date' column for period (month/year).")
    return issues

def backup_file(src_path: str) -> str:
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    base = os.path.basename(src_path)
    dst = os.path.join(BACKUP_DIR, f"{ts}__{base}")
    if os.path.exists(src_path):
        with open(src_path, "rb") as r, open(dst, "wb") as w:
            w.write(r.read())
    return dst

def list_backups() -> list[str]:
    files = sorted([f for f in os.listdir(BACKUP_DIR) if f.endswith(".xlsx")], reverse=True)
    return files

def restore_backup(backup_filename: str) -> str:
    src = os.path.join(BACKUP_DIR, backup_filename)
    if backup_filename.endswith("01.xlsx"):
        tgt = TXN_DISK_PATH
    elif backup_filename.endswith("02_budget.xlsx"):
        tgt = BDG_DISK_PATH
    else:
        raise ValueError("Backup filename does not match known targets.")
    with open(src, "rb") as r, open(tgt, "wb") as w:
        w.write(r.read())
    return tgt


from metrics import kpis, monthly_pnl
from charts import kpi_card_md, inject_watermark
def kpi_with_hint(html, hint):
    # Wrap KPI card HTML with a title tooltip
    return f'<div title="{hint}">{html}</div>'


# -----------------------------
# GLOBAL SETTINGS / CONSTANTS
# -----------------------------
st.set_page_config(page_title="Financial Performance (Redesign)", layout="wide")

# Cash balance override (as at July end)
CASH_BALANCE_OVERRIDE = 30_958_792.17
CASH_BALANCE_LABEL = "Bank Balance "

# Styling
st.markdown("""
<style>
.block-container { max-width: 1550px; padding-top: .4rem; padding-bottom: .4rem; }
.tile { background:#fff; border-radius:12px; padding:16px; box-shadow:0 2px 6px rgba(0,0,0,.05); margin-bottom:10px; }
.risk { border-left:4px solid #ef4444; }
.opp  { border-left:4px solid #10b981; }
</style>
""", unsafe_allow_html=True)


# -----------------------------
# LOAD DATA
# -----------------------------
try:
    tx, bd = load_all()
except Exception as e:
    st.error(f"Data loading error: {e}")
    st.stop()

# Ensure period columns exist
if "date" in tx.columns:
    tx["date"] = pd.to_datetime(tx["date"], errors="coerce")
tx["year"] = tx["date"].dt.year
tx["month"] = tx["date"].dt.month
if not bd.empty and "date" in bd.columns:
    bd["date"] = pd.to_datetime(bd["date"], errors="coerce")
    bd["year"] = bd["date"].dt.year
    if "month" not in bd.columns:
        bd["month"] = bd["date"].dt.month

MONTHS = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"]

def monthly_series(df, value_col="signed_amount"):
    if df.empty or not {"year","month"}.issubset(df.columns):
        return pd.Series(dtype=float)
    out = (df.groupby(["year","month"], as_index=False)[value_col].sum()
             .assign(Period=lambda x: pd.to_datetime(dict(year=x.year, month=x.month, day=1)))
             .sort_values("Period"))
    return out.set_index("Period")[value_col]

def simple_linear_forecast(s, periods=6):
    if s.empty: return pd.Series(dtype=float)
    y = s.values.astype(float)
    x = np.arange(len(y))
    try:
        a, b = np.polyfit(x, y, 1)
    except Exception:
        return pd.Series(dtype=float)
    future = [a * (len(y)-1+k) + b for k in range(1, periods+1)]
    idx = pd.date_range(s.index.max() + pd.offsets.MonthBegin(1), periods=periods, freq="MS")
    return pd.Series(future, index=idx)

def cash_in_out(tx_df):
    if tx_df.empty:
        return pd.DataFrame(columns=["Period","CashIn","CashOut","Net"])
    s_rev = monthly_series(tx_df[tx_df["account_group"].eq("Revenue")])
    s_exp = monthly_series(tx_df[tx_df["account_group"].isin(["COGS","OPEX"])])
    df = pd.DataFrame({"Period": s_rev.index.union(s_exp.index)}).set_index("Period")
    df["CashIn"]  = s_rev.reindex(df.index).fillna(0.0)
    df["CashOut"] = (-s_exp).reindex(df.index).fillna(0.0)
    df["Net"] = df["CashIn"] - df["CashOut"]
    return df.reset_index()

def cash_runway(cash_balance, cashflow_df, months_window=3):
    if cashflow_df.empty:
        return None
    tail = cashflow_df.tail(months_window)
    avg_burn = max(1e-9, tail["CashOut"].mean() - tail["CashIn"].mean())
    return (cash_balance / avg_burn) if avg_burn > 0 else np.inf

# ---------- NEW: strict drivers (MoM) ----------
def compute_driver_deltas(df: pd.DataFrame, topn: int = 3):
    """
    Top MoM drivers strictly separated by sign.
    Returns pos_top (delta>0) and neg_top (delta<0).
    """
    if df.empty or not {"year","month","signed_amount"}.issubset(df.columns):
        return pd.DataFrame(columns=["label","delta"]), pd.DataFrame(columns=["label","delta"])

    # most recent (year, month) available in slice
    last = df.dropna(subset=["year","month"]).sort_values(["year","month"]).tail(1)
    if last.empty:
        return pd.DataFrame(columns=["label","delta"]), pd.DataFrame(columns=["label","delta"])
    y, m = int(last["year"].iloc[0]), int(last["month"].iloc[0])
    prev_y, prev_m = (y, m-1) if m > 1 else (y-1, 12)

    label_col = next((c for c in ["ACCOUNT","PROJECT","NAME","Short_CLASS","CLASS"] if c in df.columns),
                     "account_group")

    curr = (df[(df["year"].eq(y)) & (df["month"].eq(m))]
              .groupby(label_col, as_index=False)["signed_amount"].sum()
              .rename(columns={"signed_amount":"curr"}))
    prev = (df[(df["year"].eq(prev_y)) & (df["month"].eq(prev_m))]
              .groupby(label_col, as_index=False)["signed_amount"].sum()
              .rename(columns={"signed_amount":"prev"}))

    t = curr.merge(prev, on=label_col, how="left").fillna({"prev": 0.0})
    t["delta"] = t["curr"] - t["prev"]
    t = t.rename(columns={label_col: "label"})

    pos_top = (t[t["delta"] > 0].sort_values("delta", ascending=False)
                  .head(topn)[["label","delta"]])
    neg_top = (t[t["delta"] < 0].sort_values("delta", ascending=True)
                  .head(topn)[["label","delta"]])
    return pos_top, neg_top

# -----------------------------
# NAV
# -----------------------------
with st.sidebar:
    page = option_menu(
        menu_title=None,
        options=["Executive Overview","Revenue Analysis","Expense & Margin","Cash & Liquidity","Insights","P&L (Table)"],
        icons=["speedometer2","bar-chart-line","cash-coin","wallet2","lightning","table"],
        default_index=0,
        styles={
            "container": {"padding": "0!important"},
            "icon": {"font-size": "18px"},
            "nav-link": {"font-size": "15px", "padding": "10px 8px"},
            "nav-link-selected": {"background-color": "#f0f2f6"},
        },
    )

# Global Year filter (skip on Revenue page)
if page != "Revenue Analysis":
    years = sorted(tx["year"].dropna().unique())
    gy1, _ = st.columns([1,5])
    with gy1:
        g_year = st.selectbox("Year", ["All"] + list(years), index=len(years) if years else 0)
else:
    g_year = "All"

DF = tx if g_year == "All" else tx[tx["year"].eq(int(g_year))]
MN = monthly_pnl(DF)

# -----------------------------
# Upload Center (safe with preview, undo, backups)
# -----------------------------
with st.sidebar.expander("Upload / Manage Data", expanded=False):
    up_tx = st.file_uploader("Upload Transactions (01.xlsx / .csv)", type=["xlsx", "xls", "csv"], key="u_tx")
    up_bd = st.file_uploader("Upload Budget (02_budget.xlsx / .csv)", type=["xlsx", "xls", "csv"], key="u_bd")
    mode  = st.radio("Apply mode", ["Append", "Replace"], horizontal=True, key="u_mode")

    # Keep original in session for undo
    if "tx_original" not in st.session_state:
        st.session_state.tx_original = tx.copy()
    if "bd_original" not in st.session_state:
        st.session_state.bd_original = bd.copy()

    # Staging area (preview only)
    def _read_any(uploaded):
        if uploaded is None:
            return None
        if uploaded.name.lower().endswith(".csv"):
            return pd.read_csv(uploaded)
        return pd.read_excel(uploaded)

    st.markdown("**Step 1 — Preview & Validate**")
    if up_tx is not None:
        tx_new = _norm_cols(_read_any(up_tx))
        st.caption(f"Transactions preview ({len(tx_new):,} rows):")
        st.dataframe(tx_new.head(10), use_container_width=True)
        probs = validate_transactions(tx_new)
        if probs:
            st.error("Transactions validation failed:\n- " + "\n- ".join(probs))
        else:
            st.success("Transactions look OK.")
    if up_bd is not None:
        bd_new = _norm_cols(_read_any(up_bd))
        st.caption(f"Budget preview ({len(bd_new):,} rows):")
        st.dataframe(bd_new.head(10), use_container_width=True)
        probs_b = validate_budget(bd_new)
        if probs_b:
            st.warning("Budget validation warnings:\n- " + "\n- ".join(probs_b))
        else:
            st.success("Budget looks OK.")

    colA, colB = st.columns(2)
    with colA:
        if st.button("Apply to session", type="primary", use_container_width=True):
            try:
                if up_tx is not None:
                    if validate_transactions(tx_new):
                        st.stop()
                    if mode == "Replace":
                        tx = tx_new.copy()
                    else:
                        tx = pd.concat([tx, tx_new], ignore_index=True)
                if up_bd is not None:
                    if validate_budget(bd_new):
                        # Not fatal; still allow apply, but you’ve been warned
                        pass
                    if mode == "Replace":
                        bd = bd_new.copy()
                    else:
                        bd = pd.concat([bd, bd_new], ignore_index=True)

                # recompute period columns
                if "date" in tx.columns:
                    tx["date"] = pd.to_datetime(tx["date"], errors="coerce")
                elif "Date" in tx.columns:
                    tx["date"] = pd.to_datetime(tx["Date"], errors="coerce")
                tx["year"] = tx["date"].dt.year
                tx["month"] = tx["date"].dt.month

                if not bd.empty:
                    if "date" in bd.columns:
                        bd["date"] = pd.to_datetime(bd["date"], errors="coerce")
                    elif "Date" in bd.columns:
                        bd["date"] = pd.to_datetime(bd["Date"], errors="coerce")
                    bd["year"] = bd["date"].dt.year
                    if "month" not in bd.columns:
                        bd["month"] = bd["date"].dt.month

                # update session “current” copies used by pages
                st.session_state["tx_current"] = tx.copy()
                st.session_state["bd_current"] = bd.copy()
                st.success("Applied to this session. Not saved to disk yet.")
            except Exception as e:
                st.error(f"Apply failed: {e}")

    with colB:
        if st.button("Undo session changes", use_container_width=True):
            tx = st.session_state.tx_original.copy()
            bd = st.session_state.bd_original.copy()
            st.session_state["tx_current"] = tx.copy()
            st.session_state["bd_current"] = bd.copy()
            st.success("Reverted session to original data loaded from disk.")

    st.markdown("---")
    st.markdown("**Step 2 — Persist to disk (with backups)**")

    c1, c2 = st.columns(2)
    with c1:
        if st.button("Save transactions to disk", use_container_width=True):
            try:
                backup = backup_file(TXN_DISK_PATH)
                tx.to_excel(TXN_DISK_PATH, index=False)
                st.success(f"Saved 01.xlsx ({len(tx):,} rows). Backup: {os.path.basename(backup)}")
            except Exception as e:
                st.error(f"Save failed: {e}")
    with c2:
        if st.button("Save budget to disk", use_container_width=True):
            try:
                backup = backup_file(BDG_DISK_PATH)
                bd.to_excel(BDG_DISK_PATH, index=False)
                st.success(f"Saved 02_budget.xlsx ({len(bd):,} rows). Backup: {os.path.basename(backup)}")
            except Exception as e:
                st.error(f"Save failed: {e}")

    st.markdown("---")
    st.markdown("**Restore from backup**")
    backups = list_backups()
    if backups:
        pick = st.selectbox("Choose a backup to restore", backups, index=0)
        if st.button("Restore selected backup", use_container_width=True):
            try:
                tgt = restore_backup(pick)
                st.success(f"Restored {os.path.basename(pick)} → {os.path.basename(tgt)}")
                st.info("Reloading from disk is recommended after a restore (use the button below).")
            except Exception as e:
                st.error(f"Restore failed: {e}")
    else:
        st.caption("No backups found yet.")

    if st.button("Reload fresh from disk (discard session)", use_container_width=True):
        try:
            tx, bd = load_all()  # from data.py
            st.session_state.tx_original = tx.copy()
            st.session_state.bd_original = bd.copy()
            st.session_state["tx_current"] = tx.copy()
            st.session_state["bd_current"] = bd.copy()
            st.success("Reloaded from disk.")
        except Exception as e:
            st.error(f"Reload failed: {e}")

# -----------------------------
# PAGE 1 — EXEC OVERVIEW (clean)
# -----------------------------
if page == "Executive Overview":
    st.title("Executive Overview")

    # ---- KPIs ----
    k = kpis(DF)
    total_rev = float(k.get("Revenue", 0.0))
    gross_profit = float(k.get("Gross Profit", 0.0))
    net_profit = float(k.get("EBIT", 0.0))
    gp_margin = (gross_profit / total_rev * 100.0) if total_rev else 0.0
    np_margin = (net_profit / total_rev * 100.0) if total_rev else 0.0

    if g_year != "All":
        prev_year = int(g_year) - 1
        R_this = DF[DF["account_group"].eq("Revenue")]["signed_amount"].sum()
        R_prev = tx[(tx["year"].eq(prev_year)) & (tx["account_group"].eq("Revenue"))]["signed_amount"].sum()
        yoy = ((R_this / R_prev - 1.0) * 100.0) if R_prev else 0.0
    else:
        yoy = 0.0

    if not bd.empty:
        B_slice = bd if g_year == "All" else bd[bd["year"].eq(int(g_year))]
        b_rev = B_slice[B_slice["account_group"].eq("Revenue")]["budget_amount"].sum()
        var_vs_budget = total_rev - b_rev
    else:
        var_vs_budget = 0.0

    cash_balance = CASH_BALANCE_OVERRIDE  # fixed balance

    k1, k2, k3, k4, k5, k6 = st.columns(6)
    k1.markdown(kpi_with_hint(
        kpi_card_md("Revenue", total_rev, "#16a34a" if total_rev >= 0 else "#ef4444", f"GP% {gp_margin:.1f}%"),
        "Total recognized revenue in the selected period."
    ), unsafe_allow_html=True)

    k2.markdown(kpi_with_hint(
        kpi_card_md("GP%", gp_margin, "#2563eb", f"GP {gross_profit:,.0f}"),
        "Gross profit margin = (Revenue − COGS) / Revenue."
    ), unsafe_allow_html=True)

    k3.markdown(kpi_with_hint(
        kpi_card_md("NP%", np_margin, "#6d28d9", f"NP {net_profit:,.0f}"),
        "Net profit margin = (Gross Profit − OPEX) / Revenue."
    ), unsafe_allow_html=True)

    k4.markdown(kpi_with_hint(
        kpi_card_md(CASH_BALANCE_LABEL, cash_balance, "#0ea5e9", ""),
        "Hard-set bank balance you provided; used for runway."
    ), unsafe_allow_html=True)

    k5.markdown(kpi_with_hint(
        kpi_card_md("YoY Growth", yoy, "#10b981" if yoy >= 0 else "#ef4444",
                    f"vs {int(g_year) - 1 if g_year != 'All' else 'LY'}"),
        "Revenue growth vs same period last year."
    ), unsafe_allow_html=True)

    k6.markdown(kpi_with_hint(
        kpi_card_md("Var vs Budget", var_vs_budget, "#10b981" if var_vs_budget >= 0 else "#ef4444", "Actual − Budget"),
        "Revenue variance vs budget in the selected period."
    ), unsafe_allow_html=True)

    st.markdown("")

    # ---- Cash Allocation (Overhead / Risk / Projects) ----
    st.subheader("Cash Allocation (of Bank Balance)")

    # Defaults once per session
    if "alloc_overhead" not in st.session_state:
        st.session_state.alloc_overhead = round(cash_balance * 0.40, 2)
        st.session_state.alloc_risk     = round(cash_balance * 0.20, 2)
        st.session_state.alloc_projects = round(cash_balance * 0.40, 2)

    c_left, c_right = st.columns([1.1, 1.0], gap="large")

    with c_right:
        st.caption(f"Target total: **₦{cash_balance:,.2f}**")
        a_over = st.number_input("Overhead (₦)", min_value=0.0, value=float(st.session_state.alloc_overhead), step=50_000.0, key="alloc_overhead_in")
        a_risk = st.number_input("Risk (₦)",     min_value=0.0, value=float(st.session_state.alloc_risk),     step=50_000.0, key="alloc_risk_in")
        a_proj = st.number_input("Projects (₦)", min_value=0.0, value=float(st.session_state.alloc_projects), step=50_000.0, key="alloc_projects_in")

        # Auto-normalize to match the fixed cash balance
        entered_sum = a_over + a_risk + a_proj
        if entered_sum <= 0:
            a_over = round(cash_balance * 0.40, 2)
            a_risk = round(cash_balance * 0.20, 2)
            a_proj = round(cash_balance * 0.40, 2)
            norm_note = "No amounts entered; using default split."
        elif abs(entered_sum - cash_balance) > 1:
            scale = cash_balance / entered_sum
            a_over = round(a_over * scale, 2)
            a_risk = round(a_risk * scale, 2)
            a_proj = round(a_proj * scale, 2)
            norm_note = "Adjusted to match total bank balance."
        else:
            norm_note = "Exact match."

        st.session_state.alloc_overhead = a_over
        st.session_state.alloc_risk     = a_risk
        st.session_state.alloc_projects = a_proj

        alloc_df = pd.DataFrame({
            "Bucket": ["Overhead","Risk","Projects","Total"],
            "Amount (₦)": [a_over, a_risk, a_proj, a_over + a_risk + a_proj]
        })
        st.dataframe(alloc_df.style.format({"Amount (₦)":"₦{:,.2f}"}), use_container_width=True)
        st.caption(norm_note)

    with c_left:
        fig_alloc = go.Figure(go.Pie(
            labels=["Overhead","Risk","Projects"],
            values=[a_over, a_risk, a_proj],
            hole=0.55,
            textinfo="label+percent",
            hovertemplate="%{label}<br>₦%{value:,.2f}<extra></extra>"
        ))
        fig_alloc.update_layout(margin=dict(l=0,r=0,t=10,b=0))
        st.plotly_chart(fig_alloc, use_container_width=True, key="alloc_donut")

    st.markdown("---")

    # ---- Forecast to Year-End (only once) ----
    st.subheader("Forecast to Year-End")
    rev_series = monthly_series(DF[DF["account_group"].eq("Revenue")])
    fc = simple_linear_forecast(rev_series, periods=6)
    fig_fc = go.Figure()
    if not rev_series.empty:
        fig_fc.add_scatter(x=rev_series.index, y=rev_series.values, mode="lines+markers", name="Actual")
    if not fc.empty:
        fig_fc.add_scatter(x=fc.index, y=fc.values, mode="lines+markers", name="Forecast")
    fig_fc.update_layout(margin=dict(l=0,r=0,t=10,b=0), yaxis_title="₦", showlegend=True)
    st.plotly_chart(fig_fc, use_container_width=True, key="forecast_overview")

# -----------------------------
# PAGE 2 — REVENUE
# -----------------------------
elif page == "Revenue Analysis":
    st.title("Revenue Analysis")

    R = DF[DF["account_group"].eq("Revenue")].copy()
    years_rev = sorted(R["year"].dropna().unique())

    f1, f2 = st.columns([1.4, 2.2])
    with f1:
        year_rev = st.selectbox("Year", options=["All"] + list(years_rev), index=len(years_rev) if years_rev else 0)
    with f2:
        month_sel = st.multiselect("Month", options=list(range(1,13)), default=list(range(1,13)), format_func=lambda m: MONTHS[m-1])

    S = R.copy()
    if year_rev != "All":
        S = S[S["year"].eq(int(year_rev))]
    if month_sel:
        S = S[S["month"].isin(month_sel)]

    st.subheader("Monthly Revenue Trend")
    trend = (S.groupby(["year","month"], as_index=False)["signed_amount"].sum()
               .assign(Period=lambda x: pd.to_datetime(dict(year=x.year, month=x.month, day=1)))
               .sort_values("Period"))
    fig_tr = go.Figure()
    fig_tr.add_bar(x=trend["Period"], y=trend["signed_amount"], name="Revenue")
    fig_tr.update_layout(margin=dict(l=0,r=0,t=10,b=0), yaxis_title="₦", xaxis_title="Month")
    st.plotly_chart(fig_tr, use_container_width=True)

    st.subheader("Top 5 Projects — Contribution")
    proj_col = next((c for c in ["ACCOUNT","Project","PROJECT"] if c in S.columns), None)
    if proj_col is None:
        st.info("No project column found (ACCOUNT/PROJECT).")
    else:
        by_proj = (S.groupby(proj_col, as_index=False)["signed_amount"].sum()
                     .rename(columns={"signed_amount":"Revenue"})
                     .sort_values("Revenue", ascending=False))
        top5 = by_proj.head(5).copy()
        total_slice = by_proj["Revenue"].sum() or 1.0
        top5["%Share"] = top5["Revenue"] / total_slice * 100.0
        fig_top = go.Figure()
        fig_top.add_bar(y=top5[proj_col][::-1], x=top5["Revenue"][::-1], orientation="h",
                        text=[f"{v:,.0f} ({s:.1f}%)" for v, s in zip(top5["Revenue"][::-1], top5["%Share"][::-1])],
                        textposition="outside")
        fig_top.update_layout(margin=dict(l=0,r=0,t=10,b=0), xaxis_title="₦", yaxis_title="Project")
        st.plotly_chart(fig_top, use_container_width=True)
        st.caption(f"Top 5 projects = **{top5['%Share'].sum():.1f}%** of revenue in selection.")

    st.subheader("Customer Concentration")
    cust_col = next((c for c in ["NAME","ACCOUNT","Customer","CUSTOMER"] if c in S.columns), None)
    if cust_col is None:
        st.info("No customer column found.")
    else:
        by_cust = (S.groupby(cust_col, as_index=False)["signed_amount"].sum()
                     .rename(columns={"signed_amount":"Revenue"})
                     .sort_values("Revenue", ascending=False))
        top = by_cust.head(10)
        total = by_cust["Revenue"].sum() or 1.0
        top["%Share"] = top["Revenue"]/total*100
        risky = top[top["%Share"] > 20.0]
        fig_c = go.Figure()
        fig_c.add_bar(y=top[cust_col][::-1], x=top["Revenue"][::-1], orientation="h")
        fig_c.update_layout(margin=dict(l=0,r=0,t=10,b=0), xaxis_title="₦", yaxis_title="Customer")
        st.plotly_chart(fig_c, use_container_width=True)
        if not risky.empty:
            st.warning(f"Concentration risk: {len(risky)} customer(s) > 20% share.")

    st.subheader("6-Month Revenue Forecast")
    series = monthly_series(S)
    fc = simple_linear_forecast(series, periods=6)
    fig_fc = go.Figure()
    if not series.empty:
        fig_fc.add_scatter(x=series.index, y=series.values, mode="lines+markers", name="Actual")
    if not fc.empty:
        fig_fc.add_scatter(x=fc.index, y=fc.values, mode="lines+markers", name="Forecast")
    fig_fc.update_layout(margin=dict(l=0,r=0,t=10,b=0), yaxis_title="₦")
    st.plotly_chart(fig_fc, use_container_width=True)

# -----------------------------
# PAGE 3 — EXPENSE & MARGIN
# -----------------------------
elif page == "Expense & Margin":
    st.title("Expense & Margin Analysis")

    EXP = DF[DF["account_group"].isin(["COGS","OPEX"])].copy()
    EXP["abs_amount"] = EXP["signed_amount"].abs()

    st.subheader("COGS & OPEX (Monthly, Stacked)")
    exp_m = (EXP.groupby(["year","month","account_group"], as_index=False)["abs_amount"].sum()
               .assign(Period=lambda x: pd.to_datetime(dict(year=x.year, month=x.month, day=1))))
    fig_e = go.Figure()
    for g in ["COGS","OPEX"]:
        tmp = exp_m[exp_m["account_group"].eq(g)]
        fig_e.add_bar(x=tmp["Period"], y=tmp["abs_amount"], name=g)
    fig_e.update_layout(barmode="stack", margin=dict(l=0,r=0,t=10,b=0), yaxis_title="₦")
    st.plotly_chart(fig_e, use_container_width=True)

    st.subheader("Profit Bridge (Revenue → GP → NP)")
    rev_total  = DF[DF["account_group"].eq("Revenue")]["signed_amount"].sum()
    cogs_total = -DF[DF["account_group"].eq("COGS")]["signed_amount"].sum()
    opex_total = -DF[DF["account_group"].eq("OPEX")]["signed_amount"].sum()
    gp_total = rev_total - cogs_total
    np_total = gp_total - opex_total
    fig_w = go.Figure(go.Waterfall(measure=["relative","relative","relative","total"],
                                   x=["Revenue","− COGS","− OPEX","Net Profit"],
                                   y=[rev_total, -cogs_total, -opex_total, np_total]))
    fig_w.update_layout(margin=dict(l=0,r=0,t=10,b=0), yaxis_title="₦")
    st.plotly_chart(fig_w, use_container_width=True)

    st.subheader("Expense Breakdown (% of Revenue)")
    rev = max(1e-9, rev_total)
    if "Short_CLASS" in EXP.columns:
        by = EXP.groupby("Short_CLASS", as_index=False)["abs_amount"].sum()
    else:
        by = EXP.groupby("CLASS", as_index=False)["abs_amount"].sum()
    by["% of Rev"] = by["abs_amount"]/rev*100
    by = by.sort_values("abs_amount", ascending=False).head(15)
    st.dataframe(by.rename(columns={"abs_amount":"Amount"}).style.format({"Amount":"₦{:,.0f}", "% of Rev":"{:.1f}%"}),
                 use_container_width=True)

# -----------------------------
# PAGE 4 — CASH & LIQUIDITY
# -----------------------------
elif page == "Cash & Liquidity":
    st.title("Cash Flow & Liquidity")

    CF = cash_in_out(DF)
    st.subheader("Monthly Cash In / Cash Out")
    if CF.empty:
        st.info("Not enough information to derive cash flows.")
    else:
        fig_c = go.Figure()
        fig_c.add_bar(x=CF["Period"], y=CF["CashIn"], name="Cash In")
        fig_c.add_bar(x=CF["Period"], y=CF["CashOut"], name="Cash Out")
        fig_c.add_scatter(x=CF["Period"], y=CF["Net"], mode="lines+markers", name="Net")
        fig_c.update_layout(barmode="group", margin=dict(l=0,r=0,t=10,b=0), yaxis_title="₦")
        st.plotly_chart(fig_c, use_container_width=True)

    # OVERRIDDEN CASH BALANCE + runway
    cash_balance = CASH_BALANCE_OVERRIDE
    runway = cash_runway(cash_balance, CF, months_window=3)

    c1, c2, c3 = st.columns(3)
    c1.markdown(kpi_card_md(CASH_BALANCE_LABEL, cash_balance, "#0ea5e9", ""), unsafe_allow_html=True)
    c2.markdown(kpi_card_md("Avg Burn (3m)",
                            (CF["CashOut"].tail(3).mean() - CF["CashIn"].tail(3).mean()) if not CF.empty else 0.0,
                            "#ef4444", "Out − In"), unsafe_allow_html=True)
    runway_txt = ("No burn / insufficient data" if (runway is None or np.isinf(runway))
                  else f"~{runway:.1f} months")
    c3.markdown(kpi_card_md("Cash Runway",
                            runway if runway and not np.isinf(runway) else 0.0,
                            "#10b981" if runway and runway>6 else "#eab308" if runway and runway>3 else "#ef4444",
                            runway_txt), unsafe_allow_html=True)

# -----------------------------
# PAGE 5 — INSIGHTS
# -----------------------------
elif page == "Insights":
    st.title("Actionable Insights")

    k = kpis(DF)
    rev = float(k.get("Revenue", 0.0))
    gp  = float(k.get("Gross Profit", 0.0))
    npf = float(k.get("EBIT", 0.0))
    gp_pct = (gp/rev*100) if rev else 0.0
    np_pct = (npf/rev*100) if rev else 0.0

    CF = cash_in_out(DF)
    runway = cash_runway(CASH_BALANCE_OVERRIDE, CF)
    st.caption(f"{CASH_BALANCE_LABEL}: ₦{CASH_BALANCE_OVERRIDE:,.2f}")

    lines = [f"Revenue: ₦{rev:,.0f}; GP% {gp_pct:.1f}; NP% {np_pct:.1f}."]
    if not bd.empty:
        b_rev = (bd[bd["account_group"].eq("Revenue")]["budget_amount"].sum())
        if b_rev:
            vv = rev - b_rev
            lines.append(("Revenue exceeded budget by " if vv>=0 else "Revenue trailed budget by ") + f"₦{vv:,.0f}.")
    st.write(" ".join(lines))

    st.markdown("### Suggested Next Actions")
    suggestions = []
    if gp_pct < 20:
        suggestions.append("Gross margin below 20%: review pricing and unit cost on top-3 projects.")
    if runway and runway < 3:
        suggestions.append("Extend runway: negotiate supplier terms (+7 days), accelerate collections, defer non-essential spend.")
    if suggestions:
        for s in suggestions:
            st.markdown(f"- {s}")
    # If allocation was edited on Overview, surface it here
    if all(k in st.session_state for k in ("alloc_overhead", "alloc_risk", "alloc_projects")):
        st.markdown(
            f"**Cash Allocation (current):** Overhead ₦{st.session_state.alloc_overhead:,.0f} | "
            f"Risk ₦{st.session_state.alloc_risk:,.0f} | "
            f"Projects ₦{st.session_state.alloc_projects:,.0f}"
        )

    else:
        st.info("No urgent actions detected from current slice.")

# -----------------------------
# P&L (Table) — Independent year selector using RAW tx
# -----------------------------
else:
    st.title("Statement of Profit & Loss")

    years_all = sorted(tx["year"].dropna().unique())
    if not years_all:
        st.info("No data available.")
        st.stop()
    coly, _ = st.columns([1,4])
    with coly:
        pl_year = st.selectbox("Year", years_all, index=len(years_all)-1, key="pl_year_stmt")

    Y = tx[tx["year"].eq(int(pl_year))].copy()
    if Y.empty:
        st.info("No rows for the selected year.")
        st.stop()

    line_col = next((c for c in ["ACCOUNT","PROJECT","Project","NAME","Short_CLASS","CLASS"] if c in Y.columns), None)
    if line_col is None:
        line_col = "ACCOUNT"
        Y[line_col] = "Unspecified"

    months_present = sorted(Y["month"].dropna().unique().tolist())
    month_labels = {1:"January",2:"February",3:"March",4:"April",5:"May",6:"June",7:"July",8:"August",9:"September",10:"October",11:"November",12:"December"}
    month_short  = {i:lab[:3] for i,lab in month_labels.items()}

    def build_section(df, group_name):
        part = df[df["account_group"].eq(group_name)].copy()
        if part.empty:
            cols = [month_short[m] for m in months_present] + ["Total"]
            return pd.DataFrame(columns=["Line Item"] + cols), 0.0
        g = (part.groupby([line_col, "month"], as_index=False)["signed_amount"].sum())
        g["val"] = g["signed_amount"] if group_name == "Revenue" else -g["signed_amount"]
        piv = g.pivot_table(index=line_col, columns="month", values="val", aggfunc="sum", fill_value=0.0)
        piv = piv.reindex(columns=months_present, fill_value=0.0)
        piv["Total"] = piv.sum(axis=1)
        piv.reset_index(inplace=True)
        piv.rename(columns={line_col: "Line Item", **{m: month_short[m] for m in months_present}}, inplace=True)
        subtotal = piv["Total"].sum()
        piv = piv.sort_values("Total", ascending=False)
        return piv, float(subtotal)

    rev_tbl, rev_total = build_section(Y, "Revenue")
    cogs_tbl, cogs_total = build_section(Y, "COGS")
    opex_tbl, opex_total = build_section(Y, "OPEX")

    gross_profit = rev_total - cogs_total
    net_profit   = gross_profit - opex_total

    # Opening balance = prior year's computed NP (if available)
    prev_year_np = np.nan
    prev_year = int(pl_year) - 1
    if (tx["year"] == prev_year).any():
        prevY = tx[tx["year"].eq(prev_year)]
        rev_prev  =  prevY[prevY["account_group"].eq("Revenue")]["signed_amount"].sum()
        cogs_prev = -prevY[prevY["account_group"].eq("COGS")]["signed_amount"].sum()
        opex_prev = -prevY[prevY["account_group"].eq("OPEX")]["signed_amount"].sum()
        gp_prev   = rev_prev - cogs_prev
        prev_year_np = gp_prev - opex_prev

    # Assemble final table
    def assemble_table():
        blocks = []

        if pd.notna(prev_year_np) and prev_year_np != 0:
            open_row = {"Line Item": f"Profit & loss balance as at December 31st {prev_year}"}
            open_row.update({month_short[m]: 0.0 for m in months_present})
            open_row["Total"] = prev_year_np
            blocks.append(pd.DataFrame([open_row]))

        header_rev = {"Line Item": "Trading Income", **{month_short[m]: "" for m in months_present}, "Total": ""}
        blocks.append(pd.DataFrame([header_rev]))
        blocks.append(rev_tbl)
        subtotal_rev = {"Line Item": "Total Trading Income",
                        **{month_short[m]: rev_tbl[month_short[m]].sum() if not rev_tbl.empty else 0.0 for m in months_present},
                        "Total": rev_total}
        blocks.append(pd.DataFrame([subtotal_rev]))

        header_cogs = {"Line Item": "Cost of Sales", **{month_short[m]: "" for m in months_present}, "Total": ""}
        blocks.append(pd.DataFrame([header_cogs]))
        blocks.append(cogs_tbl)
        subtotal_cogs = {"Line Item": "Total Cost of Sales",
                         **{month_short[m]: cogs_tbl[month_short[m]].sum() if not cogs_tbl.empty else 0.0 for m in months_present},
                         "Total": cogs_total}
        blocks.append(pd.DataFrame([subtotal_cogs]))

        gp_row = {"Line Item": "Gross Profit",
                  **{month_short[m]: (rev_tbl[month_short[m]].sum() if not rev_tbl.empty else 0.0) -
                                     (cogs_tbl[month_short[m]].sum() if not cogs_tbl.empty else 0.0)
                     for m in months_present},
                  "Total": gross_profit}
        blocks.append(pd.DataFrame([gp_row]))

        header_opex = {"Line Item": "Operating Expenses", **{month_short[m]: "" for m in months_present}, "Total": ""}
        blocks.append(pd.DataFrame([header_opex]))
        blocks.append(opex_tbl)
        subtotal_opex = {"Line Item": "Total Operating Expenses",
                         **{month_short[m]: opex_tbl[month_short[m]].sum() if not opex_tbl.empty else 0.0 for m in months_present},
                         "Total": opex_total}
        blocks.append(pd.DataFrame([subtotal_opex]))

        np_row = {"Line Item": "Net Profit",
                  **{month_short[m]: gp_row[month_short[m]] -
                                     (opex_tbl[month_short[m]].sum() if not opex_tbl.empty else 0.0)
                     for m in months_present},
                  "Total": net_profit}
        blocks.append(pd.DataFrame([np_row]))
        return pd.concat(blocks, ignore_index=True)

    out = assemble_table()

    def currency_fmt(v):
        try: return f"₦{v:,.2f}"
        except Exception: return v

    bold_rows = out["Line Item"].isin([
        "Trading Income", "Total Trading Income",
        "Cost of Sales", "Total Cost of Sales",
        "Gross Profit",
        "Operating Expenses", "Total Operating Expenses",
        "Net Profit"
    ])

    st.dataframe(
        out.style.format(currency_fmt, subset=[c for c in out.columns if c != "Line Item"])
                 .set_properties(subset=pd.IndexSlice[bold_rows, :], **{"font-weight":"700"}),
        use_container_width=True
    )

    # Show your fixed bank balance below the P&L
    st.markdown(f"**{CASH_BALANCE_LABEL}:** ₦{CASH_BALANCE_OVERRIDE:,.2f}")

    def to_excel_bytes(df):
        buf = io.BytesIO()
        with pd.ExcelWriter(buf, engine="xlsxwriter") as writer:
            df.to_excel(writer, index=False, sheet_name=f"P&L_{pl_year}")
        buf.seek(0)
        return buf

    st.download_button("Download P&L (Excel)",
                       data=to_excel_bytes(out),
                       file_name=f"Statement_of_PL_{pl_year}.xlsx",
                       type="primary")
