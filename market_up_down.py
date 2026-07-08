# market_up_down.py
# Reads live Yahoo Finance hourly data for the symbols in tickers.txt and, for a
# user-defined period, finds each symbol's highest and lowest intraday price
# points (by hour) and writes them to a timestamped CSV.
#
# The period can be given two ways:
#   - Last N days:      uv run market_up_down.py 20
#   - Explicit range:   uv run market_up_down.py 20260629-20260711
#     (June 29, 2026 through July 11, 2026, both inclusive)
#
# Output: config/output/market-up-down-YYYYMMDDHHMM.csv
# CSV fields: symbol, high price, high date and hour, low price, low date and hour, change (high - low)
import os
import sys
import csv
import yfinance as yf
from datetime import datetime, timedelta
from dotenv import load_dotenv

load_dotenv()

USAGE = (
    "Usage: uv run market_up_down.py <period>\n"
    "  <period> is either:\n"
    "    N                    number of most recent days, e.g. 20\n"
    "    YYYYMMDD-YYYYMMDD    an explicit start-end date range (both inclusive),\n"
    "                         e.g. 20260629-20260711\n"
)


# Helper: Get absolute path relative to the script location
def get_absolute_path(path):
    if path.startswith('.'):
        script_dir = os.path.dirname(os.path.abspath(__file__))
        return os.path.join(script_dir, path.lstrip('./'))
    return path


def read_tickers(tickers_path):
    with open(get_absolute_path(tickers_path), "r") as f:
        return [line.strip() for line in f.readlines() if line.strip()]


def usage_error(message):
    """Print an error plus the usage message, then exit."""
    print(f"Error: {message}\n", file=sys.stderr)
    print(USAGE, file=sys.stderr)
    sys.exit(2)


def parse_period(argv):
    """Parse the period argument.

    Returns a tuple (query, start_date, end_date) where `query` is the dict
    passed to yfinance's history() -- either {"period": "20d"} for a
    last-N-days request or {"start": <str>, "end": <str>} for an explicit
    range -- and start_date/end_date are the inclusive datetime bounds of the
    requested period (used for the CSV columns).

    Exits with a usage message if the argument is missing or malformed.
    """
    if len(argv) != 2:
        usage_error("exactly one period argument is required")

    arg = argv[1].strip()

    # Date range: YYYYMMDD-YYYYMMDD
    if "-" in arg:
        parts = arg.split("-")
        if len(parts) != 2:
            usage_error(f"'{arg}' is not a valid YYYYMMDD-YYYYMMDD range")
        try:
            start = datetime.strptime(parts[0], "%Y%m%d")
            end = datetime.strptime(parts[1], "%Y%m%d")
        except ValueError:
            usage_error(f"'{arg}' contains an invalid date; expected YYYYMMDD-YYYYMMDD")
        if end < start:
            usage_error(f"end date must not be before start date in '{arg}'")
        # yfinance treats `end` as exclusive; add a day so the end date is included.
        query = {
            "start": start.strftime("%Y-%m-%d"),
            "end": (end + timedelta(days=1)).strftime("%Y-%m-%d"),
        }
        return query, start, end

    # Last N days
    if not arg.isdigit():
        usage_error(f"'{arg}' is not a positive number of days")
    days = int(arg)
    if days <= 0:
        usage_error("number of days must be greater than zero")
    end = datetime.now()
    start = end - timedelta(days=days)
    return {"period": f"{days}d"}, start, end


def get_high_low(ticker, query):
    """Return the highest and lowest hourly price points for a ticker.

    `query` is the dict produced by parse_period (either a period or a
    start/end range). Uses hourly (1h) intraday bars. The high point is the max
    of the hourly High column; the low point is the min of the hourly Low
    column. Returns a dict of result fields, or None if no data is available.
    """
    stock = yf.Ticker(ticker)
    hist = stock.history(interval="1h", **query)
    if hist.empty:
        return None

    high_idx = hist['High'].idxmax()
    low_idx = hist['Low'].idxmin()
    high_price = hist['High'].loc[high_idx]
    low_price = hist['Low'].loc[low_idx]

    return {
        "symbol": ticker,
        "high_price": round(float(high_price), 2),
        "high_when": high_idx.strftime("%Y-%m-%d %H:%M"),
        "low_price": round(float(low_price), 2),
        "low_when": low_idx.strftime("%Y-%m-%d %H:%M"),
        "change": round(float(high_price) - float(low_price), 2),
    }


def main():
    query, start_date, end_date = parse_period(sys.argv)
    tickers = read_tickers(os.getenv("TICKERS_PATH"))

    start_label = start_date.strftime("%m/%d/%Y")
    end_label = end_date.strftime("%m/%d/%Y")

    if "period" in query:
        period_desc = f"last {query['period']} ({start_label} - {end_label})"
    else:
        period_desc = f"{start_label} through {end_label}"

    output_dir = os.getenv("OUTPUT_PATH")
    timestamp = datetime.now().strftime("%Y%m%d%H%M")
    output_path = os.path.join(output_dir, f"market-up-down-{timestamp}.csv")

    header = [
        "symbol",
        "high_price",
        "high_date_hour",
        "low_price",
        "low_date_hour",
        "change",
        "start_date",
        "end_date",
    ]

    print(f"[{datetime.now()}] Fetching hourly data ({period_desc}) for {len(tickers)} symbols...")
    with open(output_path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(header)
        for ticker in tickers:
            try:
                row = get_high_low(ticker, query)
            except Exception as e:
                print(f"  {ticker}: error fetching data - {e}")
                continue
            if row is None:
                print(f"  {ticker}: no data available")
                continue
            writer.writerow([
                row["symbol"],
                row["high_price"],
                row["high_when"],
                row["low_price"],
                row["low_when"],
                row["change"],
                start_label,
                end_label,
            ])
            print(
                f"  {ticker}: high {row['high_price']} @ {row['high_when']}, "
                f"low {row['low_price']} @ {row['low_when']}, change {row['change']}"
            )

    print(f"[{datetime.now()}] Wrote output to {output_path}")


if __name__ == "__main__":
    main()
