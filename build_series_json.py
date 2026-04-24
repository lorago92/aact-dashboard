"""
Build series.json — macro indicators powering the agostinilorenzo.com
/macrotrends dashboard. Six thematic charts:

  1. Growth & activity      — real GDP, industrial production, retail sales
  2. Labor market           — unemployment, NFP, initial claims, job openings
  3. Inflation & expectations — CPI, core CPI, core PCE, 10Y breakeven, Michigan 1Y
  4. Rates & yield curve    — Fed Funds, 2Y, 10Y, 10Y-2Y spread, 30Y mortgage
  5. Financial conditions   — NFCI, HY spread, VIX, S&P 500
  6. Consumer & housing     — sentiment, saving rate, Case-Shiller YoY, mortgage, starts

Daily FRED series are downsampled to weekly (Friday close) to keep
the GH Pages payload small (<400 KB uncompressed ⇒ ~80 KB gzipped).
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
    # Growth & activity
    "GDPC1":         ("Real GDP",                                 "Billions 2017 USD SAAR",     "Q"),
    "INDPRO":        ("Industrial Production",                    "Index 2017=100 SA",          "M"),
    "RSAFS":         ("Retail Sales (Advance)",                   "Millions USD SA",            "M"),
    # Labor market
    "UNRATE":        ("Unemployment Rate",                        "%",                          "M"),
    "PAYEMS":        ("Non-Farm Payrolls",                        "Thousands SA",               "M"),
    "ICSA":          ("Initial Jobless Claims",                   "Number SA",                  "W"),
    "JTSJOL":        ("Job Openings (JOLTS)",                     "Thousands SA",               "M"),
    # Inflation & expectations
    "CPIAUCSL":      ("CPI (All Urban Consumers)",                "Index 1982-84=100 SA",       "M"),
    "CPILFESL":      ("Core CPI (ex Food & Energy)",              "Index 1982-84=100 SA",       "M"),
    "PCEPILFE":      ("Core PCE Price Index",                     "Index 2017=100 SA",          "M"),
    "T10YIE":        ("10-Year Breakeven Inflation",              "%",                          "D"),
    "MICH":          ("Michigan 1-Year Inflation Expectations",   "%",                          "M"),
    # Rates & yield curve
    "DFF":           ("Effective Fed Funds Rate",                 "%",                          "D"),
    "DGS2":          ("2-Year Treasury Yield",                    "%",                          "D"),
    "DGS10":         ("10-Year Treasury Yield",                   "%",                          "D"),
    "T10Y2Y":        ("10Y-2Y Treasury Spread",                   "%",                          "D"),
    "MORTGAGE30US":  ("30-Year Fixed Mortgage Rate",              "%",                          "W"),
    # Financial conditions
    "NFCI":          ("Chicago Fed Financial Conditions Index",   "standardized",               "W"),
    "BAMLH0A0HYM2":  ("ICE BofA High-Yield OAS",                  "%",                          "D"),
    "VIXCLS":        ("CBOE Volatility Index (VIX)",              "Index",                      "D"),
    # Consumer & housing
    "UMCSENT":       ("Michigan Consumer Sentiment",              "Index 1966:Q1=100",          "M"),
    "PSAVERT":       ("Personal Saving Rate",                     "%",                          "M"),
    "CSUSHPINSA":    ("Case-Shiller Home Price Index (20-city)",  "Index Jan 2000=100 NSA",     "M"),
    "HOUST":         ("Housing Starts",                           "Thousands SAAR",             "M"),
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
    if freq == "D":
        s = s.resample("W-FRI").last().dropna()
        effective_freq = "W"
    else:
        effective_freq = freq
    out_series[fred_id] = {
        "name": name,
        "unit": unit,
        "source": "FRED",
        "freq": effective_freq,
        "points": _points(s),
    }

# S&P 500 via yfinance — month-end close
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
