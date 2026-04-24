"""
Build series.json — a bundle of FRED macro series + S&P 500 for the
agostinilorenzo.com /macrotrends dashboard. Output schema:

  {
    "generated_at": "2026-04-24T10:17:00Z",
    "recessions":   [{"start": "YYYY-MM-DD", "end": "YYYY-MM-DD"}, ...],
    "series": {
      "<SERIES_ID>": {
        "name":   "...",
        "unit":   "...",
        "source": "FRED" | "Yahoo Finance",
        "freq":   "M" | "Q" | "D",
        "points": [{"d": "YYYY-MM-DD", "v": <float>}, ...]
      },
      ...
    }
  }
"""

import json
import math
import os
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import yfinance as yf
from fredapi import Fred

FRED_API_KEY = os.environ.get("FRED_API_KEY")
if not FRED_API_KEY:
    raise SystemExit("Missing FRED_API_KEY environment variable.")

fred = Fred(api_key=FRED_API_KEY)

SERIES_SPEC = {
    # id              name                                         unit                                  freq
    "GDP":           ("Real GDP",                                  "Billions USD SAAR",                  "Q"),
    "GFDEBTN":       ("Federal Public Debt",                       "Millions USD NSA",                   "Q"),
    "GFDEGDQ188S":   ("Federal Debt as % of GDP",                  "%",                                  "Q"),
    "CPIAUCSL":      ("CPI (All Urban Consumers)",                 "Index 1982–84=100 SA",               "M"),
    "CPILFESL":      ("Core CPI (ex Food & Energy)",               "Index 1982–84=100 SA",               "M"),
    "PPIACO":        ("PPI (All Commodities)",                     "Index 1982=100 NSA",                 "M"),
    "PCEPI":         ("PCE Price Index",                           "Index 2017=100 SA",                  "M"),
    "FEDFUNDS":      ("Effective Fed Funds Rate",                  "%",                                  "M"),
    "UNRATE":        ("Unemployment Rate",                         "%",                                  "M"),
    "PCE":           ("Personal Consumption Expenditures",         "Billions USD SAAR",                  "M"),
    "PAYEMS":        ("Non-Farm Payrolls",                         "Thousands SA",                       "M"),
    "BOPGSTB":       ("Goods & Services Trade Balance",            "Millions USD NSA",                   "M"),
    "EXPGS":         ("Exports of Goods and Services",             "Billions USD SAAR",                  "Q"),
    "IMPGS":         ("Imports of Goods and Services",             "Billions USD SAAR",                  "Q"),
    "RSAFS":         ("Retail Sales (Advance)",                    "Millions USD SA",                    "M"),
    "CSUSHPINSA":    ("Case-Shiller Home Price Index (20-city)",   "Index Jan 2000=100 NSA",             "M"),
    "PERMIT":        ("New Private Housing Units Authorized",      "Thousands SAAR",                     "M"),
    "ECIALLCIV":     ("Employment Cost Index (All Civilians)",     "Index Dec 2005=100 SA",              "Q"),
    "SAHMREALTIME":  ("Sahm Rule Recession Indicator",             "percentage points",                  "M"),
}


def _sanitize(v):
    if v is None:
        return None
    if isinstance(v, float) and math.isnan(v):
        return None
    return v


def _points(series: pd.Series) -> list:
    series = series.dropna()
    return [
        {"d": d.strftime("%Y-%m-%d"), "v": _sanitize(round(float(v), 4))}
        for d, v in series.items()
    ]


out_series: dict = {}
for fred_id, (name, unit, freq) in SERIES_SPEC.items():
    s = fred.get_series(fred_id)
    out_series[fred_id] = {
        "name": name,
        "unit": unit,
        "source": "FRED",
        "freq": freq,
        "points": _points(s),
    }

# S&P 500 via yfinance — month-end close, keeps payload tight
sp = yf.download("^GSPC", period="max", auto_adjust=True, progress=False)
if sp.empty:
    raise SystemExit("Yahoo Finance returned no S&P 500 data.")
sp_close = sp["Close"].squeeze()
sp_monthly = sp_close.resample("ME").last().dropna()
out_series["SP500"] = {
    "name": "S&P 500 (month-end close)",
    "unit": "Index",
    "source": "Yahoo Finance",
    "freq": "M",
    "points": [
        {"d": d.strftime("%Y-%m-%d"), "v": round(float(v), 2)}
        for d, v in sp_monthly.items()
    ],
}

# Recession ranges from USREC (streaks of 1)
usrec = fred.get_series("USREC").dropna()
recessions: list = []
in_rec = False
rec_start = None
prev_date = None
for d, v in usrec.items():
    if v == 1 and not in_rec:
        rec_start = d
        in_rec = True
    elif v == 0 and in_rec:
        recessions.append(
            {"start": rec_start.strftime("%Y-%m-%d"), "end": prev_date.strftime("%Y-%m-%d")}
        )
        in_rec = False
    prev_date = d
if in_rec and rec_start is not None and prev_date is not None:
    recessions.append(
        {"start": rec_start.strftime("%Y-%m-%d"), "end": prev_date.strftime("%Y-%m-%d")}
    )

out = {
    "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    "recessions": recessions,
    "series": out_series,
}

out_dir = Path("public")
out_dir.mkdir(exist_ok=True)
out_path = out_dir / "series.json"
out_path.write_text(json.dumps(out, separators=(",", ":")), encoding="utf-8")
print(f"Wrote: {out_path}  ({out_path.stat().st_size / 1024:.0f} KB, {len(out_series)} series)")
