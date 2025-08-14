import pandas as pd
import numpy as np
from pathlib import Path

# Base folder where this script sits
HERE = Path(__file__).resolve().parent

# Excel file paths (same folder as data.py)
TXN_FILE = HERE / "01.xlsx"
BUDGET_FILE = HERE / "02_budget.xlsx"

def _norm_cols(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df.columns = [str(c).strip() for c in df.columns]
    return df

def _infer_group(row) -> str | None:
    side = str(row.get("REVENUE/EXPENSES", "")).strip().lower()
    short = str(row.get("Short_CLASS", "")).strip().upper()
    full = str(row.get("CLASS", "")).strip().lower()

    if side == "revenue":
        return "Revenue"
    if side == "expenses":
        if short == "COS": return "COGS"
        if short in ("G&A", "GA", "GNA"): return "OPEX"
        if full.startswith("cost of sales"): return "COGS"
        if "general & administrative" in full or "general and administrative" in full: return "OPEX"
    return None  # ignore anything else (assets/liab/etc.)

def _ensure_period_cols(df: pd.DataFrame, date_candidates=("Date","DATE","date")) -> pd.DataFrame:
    df = df.copy()
    date_col = next((c for c in date_candidates if c in df.columns), None)
    if date_col:
        df["date"] = pd.to_datetime(df[date_col], errors="coerce")
        df["year"] = df["date"].dt.year
        df["month"] = df["date"].dt.month
    else:
        df["date"] = pd.NaT; df["year"] = pd.NA; df["month"] = pd.NA
    return df

def _require_exists(p: Path, label: str):
    if not p.exists():
        listing = "\n".join(f" - {q.name}" for q in sorted(p.parent.glob('*')))
        raise FileNotFoundError(
            f"{label} not found: {p}\n"
            f"Expected alongside data.py at: {p.parent}\n"
            f"Directory listing:\n{listing}"
        )

def load_all(txn_path: str | Path | None = None,
             budget_path: str | Path | None = None):
    txn_file = Path(txn_path) if txn_path else TXN_FILE
    budget_file = Path(budget_path) if budget_path else BUDGET_FILE

    print(f">>> load_all(): txn_file = {txn_file}")
    print(f">>> load_all(): budget_file = {budget_file}")

    _require_exists(txn_file, "Transactions file")
    # Budget optional
    if not budget_file.exists():
        print(">>> (info) Budget file not found; continuing without budget.")

    # ---- Transactions ----
    tx = pd.read_excel(txn_file)
    tx = _norm_cols(tx)

    required = {"Date", "AMOUNT", "REVENUE/EXPENSES"}
    missing = required - set(tx.columns)
    if missing:
        raise ValueError(f"01.xlsx must contain {sorted(required)}. Missing: {sorted(missing)}")

    tx = _ensure_period_cols(tx)
    if "Short_CLASS" not in tx.columns: tx["Short_CLASS"] = ""
    if "CLASS" not in tx.columns: tx["CLASS"] = ""
    tx["account_group"] = tx.apply(_infer_group, axis=1)

    def _sign(row):
        amt = pd.to_numeric(row["AMOUNT"], errors="coerce")
        if pd.isna(amt): return np.nan
        g = row["account_group"]
        if g == "Revenue": return abs(amt)
        if g in ("COGS","OPEX"): return -abs(amt)
        return np.nan

    tx["signed_amount"] = tx.apply(_sign, axis=1)
    tx = tx.dropna(subset=["signed_amount","account_group"]).reset_index(drop=True)

    # ---- Budget (optional) ----
    bd = pd.DataFrame(columns=["year","month","account_group","budget_amount"])
    if budget_file.exists():
        b = pd.read_excel(budget_file)
        b = _norm_cols(b)
        b = _ensure_period_cols(b, date_candidates=("DATE","Date","date"))
        for c in ("REVENUE/EXPENSES","Short_CLASS","CLASS"):
            if c not in b.columns: b[c] = ""
        b["account_group"] = b.apply(_infer_group, axis=1)
        b = b.dropna(subset=["account_group"])

        bud_col = "BUDGET" if "BUDGET" in b.columns else ("budget_amount" if "budget_amount" in b.columns else None)
        if bud_col is None:
            b["budget_amount"] = 0.0; bud_col = "budget_amount"

        if "month" in b.columns and b["month"].notna().any():
            bd = (b.groupby(["year","month","account_group"], as_index=False)[bud_col]
                    .sum().rename(columns={bud_col:"budget_amount"}))
        else:
            bd_y = (b.groupby(["year","account_group"], as_index=False)[bud_col]
                      .sum().rename(columns={bud_col:"budget_amount"}))
            bd_y["month"] = pd.NA
            bd = bd_y[["year","month","account_group","budget_amount"]]

    return tx, bd
