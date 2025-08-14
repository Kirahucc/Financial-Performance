import pandas as pd

def kpis(df: pd.DataFrame) -> dict:
    """
    Returns totals over the provided slice:
      Revenue (sum of Revenue rows),
      COGS, OPEX (negative values in tx are costs),
      Gross Profit = Revenue - COGS,
      EBIT (Net Profit here) = Gross Profit - OPEX
    """
    if df.empty:
        return {"Revenue": 0.0, "COGS": 0.0, "OPEX": 0.0, "Gross Profit": 0.0, "EBIT": 0.0}

    rev  = df[df["account_group"].eq("Revenue")]["signed_amount"].sum()
    cogs = -df[df["account_group"].eq("COGS")]["signed_amount"].sum()
    opex = -df[df["account_group"].eq("OPEX")]["signed_amount"].sum()
    gp   = rev - cogs
    ebit = gp - opex
    return {
        "Revenue": float(rev),
        "COGS": float(cogs),
        "OPEX": float(opex),
        "Gross Profit": float(gp),
        "EBIT": float(ebit),
    }

def monthly_pnl(df: pd.DataFrame) -> pd.DataFrame:
    """
    Builds a monthly P&L summary with columns:
    year, month, Revenue, COGS, OPEX, Gross Profit, EBIT
    """
    if df.empty or not {"year","month"}.issubset(df.columns):
        return pd.DataFrame(columns=["year","month","Revenue","COGS","OPEX","Gross Profit","EBIT"])

    base = df.groupby(["year","month","account_group"], as_index=False)["signed_amount"].sum()
    # Pivot into columns
    piv = base.pivot_table(index=["year","month"], columns="account_group",
                           values="signed_amount", aggfunc="sum", fill_value=0).reset_index()

    # In tx, costs are negative; turn them positive for reporting
    piv["Revenue"] = piv.get("Revenue", 0.0)
    piv["COGS"]    = -piv.get("COGS", 0.0)
    piv["OPEX"]    = -piv.get("OPEX", 0.0)

    piv["Gross Profit"] = piv["Revenue"] - piv["COGS"]
    piv["EBIT"]         = piv["Gross Profit"] - piv["OPEX"]

    cols = ["year","month","Revenue","COGS","OPEX","Gross Profit","EBIT"]
    for c in cols:
        if c not in piv.columns:
            piv[c] = 0.0
    return piv[cols].sort_values(["year","month"]).reset_index(drop=True)
