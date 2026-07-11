from __future__ import annotations

from pathlib import Path
from typing import Iterable

import pandas as pd

REQUIRED = ("date", "symbol", "open", "high", "low", "close", "volume")


def normalize_bars(frame: pd.DataFrame) -> pd.DataFrame:
    """Return a validated (date, symbol) indexed OHLCV frame."""
    aliases = {"日期": "date", "开盘": "open", "最高": "high", "最低": "low",
               "收盘": "close", "成交量": "volume", "成交额": "amount"}
    data = frame.rename(columns=aliases).copy()
    missing = set(REQUIRED) - set(data.columns)
    if missing:
        raise ValueError(f"missing columns: {sorted(missing)}")
    data["date"] = pd.to_datetime(data["date"], errors="raise")
    data["symbol"] = data["symbol"].astype(str).str.zfill(6)
    for col in ("open", "high", "low", "close", "volume"):
        data[col] = pd.to_numeric(data[col], errors="coerce")
    if data.duplicated(["date", "symbol"]).any():
        raise ValueError("duplicate date/symbol bars")
    if (data[["open", "high", "low", "close"]].dropna() <= 0).any().any():
        raise ValueError("OHLC prices must be positive")
    return data.sort_values(["date", "symbol"]).set_index(["date", "symbol"])


def load_csv(path: str | Path) -> pd.DataFrame:
    return normalize_bars(pd.read_csv(path))


def fetch_akshare(symbols: Iterable[str], start: str, end: str, asset: str = "etf",
                  adjust: str = "qfq") -> pd.DataFrame:
    """Fetch daily bars. AKShare is optional and should not be treated as PIT data."""
    try:
        import akshare as ak
    except ImportError as exc:
        raise RuntimeError("install the optional 'data' dependencies to fetch") from exc
    rows: list[pd.DataFrame] = []
    for raw_symbol in symbols:
        symbol = str(raw_symbol).zfill(6)
        try:
            if asset == "etf":
                part = ak.fund_etf_hist_em(symbol=symbol, period="daily", start_date=start,
                                           end_date=end, adjust=adjust)
            elif asset == "stock":
                part = ak.stock_zh_a_hist(symbol=symbol, period="daily", start_date=start,
                                          end_date=end, adjust=adjust)
            else:
                raise ValueError("asset must be 'etf' or 'stock'")
        except Exception as exc:
            raise RuntimeError(
                f"failed to fetch {asset} {symbol}; check network/proxy or use a local CSV"
            ) from exc
        part = part.copy()
        part["symbol"] = symbol
        rows.append(part)
    if not rows:
        raise ValueError("no symbols supplied")
    return normalize_bars(pd.concat(rows, ignore_index=True))


def save_bars(data: pd.DataFrame, path: str | Path) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    data.reset_index().to_csv(target, index=False)
