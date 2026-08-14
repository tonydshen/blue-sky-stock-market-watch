# market_up_down.py
# Reads live Yahoo Finance hourly data for the symbols in a tickers file and, for
# a user-defined period, finds each symbol's highest and lowest intraday price
# points (by hour) and writes them to a timestamped CSV.
#
# Both arguments are optional:
#   - Last N days:      uv run market_up_down.py -p 20
#   - Explicit range:   uv run market_up_down.py -p 20260629-20260711
#     (June 29, 2026 through July 11, 2026, both inclusive)
#   - Tickers file:     uv run market_up_down.py -f tickers-sp500-it.txt
#     (a file name only, no path; read from config/tickers)
#
# When -p is omitted the last 1 day is used; when -f is omitted the default file
# from TICKERS_PATH (config/tickers/tickers.txt) is used. With neither given:
#   uv run market_up_down.py        # 1 day of data for config/tickers/tickers.txt
#
# Output: config/output/market-up-down-YYYYMMDDHHMM.csv
# CSV fields: symbol, high price, high date and hour, low price, low date and hour,
#             change (high - low, signed: positive when the low came first and the
#             symbol rose, negative when the high came first and the symbol fell),
#             change_percent (change over the starting price -- the low for a rise,
#             the high for a fall), change_days (calendar days spanned, counting
#             both the high and low dates), implied_volatility (today's at-the-money
#             option IV in percent; blank when the symbol has no listed options),
#             expected_move_percent (IV rescaled to the change_days window),
#             realized_vs_implied (how many times larger the actual move was),
#             realized_volatility (annualized, from the hourly bars),
#             period_return_percent (first open to last close),
#             close_in_range_percent (0 = closed at the low, 100 = at the high)
import os
import sys
import csv
import math
import yfinance as yf
from datetime import datetime, timedelta
from dotenv import load_dotenv

load_dotenv()

DEFAULT_PERIOD = "1"

# Skip option expiries nearer than this when sampling implied volatility.
IV_MIN_DAYS = 7

# Hourly bars in a trading year: 6.5 session hours x 252 trading days. Used to
# annualize realized volatility so it can be read against annualized IV.
TRADING_HOURS_PER_YEAR = 6.5 * 252
MIN_BARS_FOR_VOLATILITY = 4

USAGE = (
    "Usage: uv run market_up_down.py [-f <tickers file>] [-p <period>]\n"
    "  -p, --period         optional; either:\n"
    "    N                    number of most recent days, e.g. 20\n"
    "    YYYYMMDD-YYYYMMDD    an explicit start-end date range (both inclusive),\n"
    "                         e.g. 20260629-20260711\n"
    f"                       Defaults to {DEFAULT_PERIOD} day.\n"
    "  -f, --file           optional; name of a tickers file in config/tickers,\n"
    "                       e.g. tickers-sp500-it.txt (file name only, no path).\n"
    "                       Defaults to the file named by TICKERS_PATH.\n"
)


# Helper: Get absolute path relative to the script location
def get_absolute_path(path):
    if path.startswith('.'):
        script_dir = os.path.dirname(os.path.abspath(__file__))
        return os.path.join(script_dir, path.lstrip('./'))
    return path


def round_or_none(value, digits=2):
    """Round a value, passing None through for indicators we could not compute."""
    return None if value is None else round(value, digits)


def csv_value(value):
    """Render a field for the CSV, writing an empty cell for a missing value."""
    return "" if value is None else value


def read_tickers(tickers_path):
    with open(get_absolute_path(tickers_path), "r") as f:
        return [line.strip() for line in f.readlines() if line.strip()]


def resolve_tickers_path(file_name):
    """Return the full path of the tickers file to read.

    The tickers folder is the one holding the file named by TICKERS_PATH
    (config/tickers). `file_name` is a bare file name within that folder; when
    it is None the TICKERS_PATH file itself (tickers.txt) is used.
    """
    default_path = get_absolute_path(os.getenv("TICKERS_PATH"))
    if file_name is None:
        return default_path

    if os.path.basename(file_name) != file_name:
        usage_error(f"'{file_name}' must be a file name only, without a path")

    path = os.path.join(os.path.dirname(default_path), file_name)
    if not os.path.isfile(path):
        usage_error(f"tickers file not found: {path}")
    return path


def usage_error(message):
    """Print an error plus the usage message, then exit."""
    print(f"Error: {message}\n", file=sys.stderr)
    print(USAGE, file=sys.stderr)
    sys.exit(2)


def parse_args(argv):
    """Parse the command line into (query, start_date, end_date, tickers_path).

    Both -p/--period and -f/--file are optional; each falls back to its default
    (1 day, and the TICKERS_PATH file). A bare period argument is still accepted
    for backward compatibility.
    """
    period = None
    file_name = None

    args = argv[1:]
    i = 0
    while i < len(args):
        arg = args[i]
        if arg in ("-p", "--period"):
            i += 1
            if i >= len(args):
                usage_error(f"{arg} requires a value")
            period = args[i]
        elif arg in ("-f", "--file"):
            i += 1
            if i >= len(args):
                usage_error(f"{arg} requires a value")
            file_name = args[i]
        elif arg.startswith("-"):
            usage_error(f"unknown option '{arg}'")
        elif period is None:
            period = arg
        else:
            usage_error(f"unexpected argument '{arg}'")
        i += 1

    query, start, end = parse_period(period if period is not None else DEFAULT_PERIOD)
    return query, start, end, resolve_tickers_path(file_name)


def parse_period(arg):
    """Parse the period value.

    Returns a tuple (query, start_date, end_date) where `query` is the dict
    passed to yfinance's history() -- either {"period": "20d"} for a
    last-N-days request or {"start": <str>, "end": <str>} for an explicit
    range -- and start_date/end_date are the inclusive datetime bounds of the
    requested period (used for the CSV columns).

    Exits with a usage message if the value is malformed.
    """
    arg = arg.strip()

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


def get_realized_volatility(hist):
    """Return annualized realized volatility, in percent, from the hourly bars.

    Standard deviation of hourly log returns, scaled by the number of hourly
    bars in a year. The bar-to-bar returns that straddle a session boundary
    carry the overnight gap, so the estimate covers the whole move, not just
    regular-hours drift. Returns None when there are too few bars to measure.
    """
    closes = hist['Close']
    if len(closes) < MIN_BARS_FOR_VOLATILITY:
        return None

    ratios = (closes / closes.shift(1)).dropna()
    ratios = ratios[ratios > 0]
    if len(ratios) < 2:
        return None

    sigma = ratios.apply(math.log).std()
    if not sigma or math.isnan(sigma):
        return None
    return round(float(sigma) * math.sqrt(TRADING_HOURS_PER_YEAR) * 100, 2)


def get_implied_volatility(stock, spot):
    """Return the at-the-money implied volatility, in percent, or None.

    Reads the option chain from Yahoo Finance and averages the implied
    volatility of the call and put whose strikes sit closest to `spot`, using
    the nearest expiry at least IV_MIN_DAYS out (near-dated contracts carry very
    noisy IV). Returns None for symbols with no listed options -- most ETFs,
    indexes and thinly traded names -- or when Yahoo reports no usable IV.

    Note this is a snapshot of today's option market: Yahoo does not serve
    historical IV, so the value does not line up with a past -p date range.
    """
    expiries = stock.options
    if not expiries:
        return None

    cutoff = (datetime.now() + timedelta(days=IV_MIN_DAYS)).strftime("%Y-%m-%d")
    expiry = next((e for e in expiries if e >= cutoff), expiries[-1])

    chain = stock.option_chain(expiry)
    ivs = []
    for side in (chain.calls, chain.puts):
        if side.empty or "impliedVolatility" not in side:
            continue
        atm = side.loc[(side["strike"] - spot).abs().idxmin()]
        iv = float(atm["impliedVolatility"])
        # Yahoo fills unquoted contracts with 0 (or a ~1e-5 placeholder).
        if iv > 0.0001:
            ivs.append(iv)

    if not ivs:
        return None
    return round(sum(ivs) / len(ivs) * 100, 2)


def get_high_low(ticker, query):
    """Return the highest and lowest hourly price points for a ticker.

    `query` is the dict produced by parse_period (either a period or a
    start/end range). Uses hourly (1h) intraday bars. The high point is the max
    of the hourly High column; the low point is the min of the hourly Low
    column. Returns a dict of result fields, or None if no data is available.

    `change` is the high-low spread signed by trend: positive when the low came
    first and the symbol rose to its high, negative when the high came first and
    the symbol fell to its low. `change_percent` expresses that move against the
    price it started from -- the low for a rise, the high for a fall.
    `change_days` is the calendar span the move covered, counting both the high
    and low dates (1 when they fall on the same day).

    The remaining fields put that move in context: `expected_move_percent` is
    the implied volatility rescaled from annual to the change_days window,
    `realized_vs_implied` is how many times larger the actual move was,
    `realized_volatility` is the annualized volatility of the bars themselves,
    `period_return_percent` is the plain first-open-to-last-close return, and
    `close_in_range_percent` is where the last close sits between the low (0)
    and the high (100).
    """
    stock = yf.Ticker(ticker)
    hist = stock.history(interval="1h", **query)
    if hist.empty:
        return None

    high_idx = hist['High'].idxmax()
    low_idx = hist['Low'].idxmin()
    high_price = hist['High'].loc[high_idx]
    low_price = hist['Low'].loc[low_idx]

    # Signed spread: downward trend (high first, low after) reads as negative.
    change = float(high_price) - float(low_price)
    if low_idx > high_idx:
        change = -change

    # Measure the move against where it started: the high for a fall, the low
    # for a rise.
    base = float(high_price) if change < 0 else float(low_price)
    change_percent = (change / base * 100) if base else 0.0

    # Inclusive calendar span, so a move within a single day counts as 1.
    change_days = abs((high_idx.date() - low_idx.date()).days) + 1

    last_close = float(hist['Close'].iloc[-1])

    # Options are a separate request and not every symbol has them; a failure
    # here should not cost us the price row.
    try:
        implied_volatility = get_implied_volatility(stock, last_close)
    except Exception:
        implied_volatility = None

    # IV is quoted annualized; rescale it to the window the move happened in so
    # the two are read on the same footing.
    expected_move_percent = None
    realized_vs_implied = None
    if implied_volatility:
        expected_move_percent = implied_volatility * math.sqrt(change_days / 365)
        if expected_move_percent:
            realized_vs_implied = abs(change_percent) / expected_move_percent

    realized_volatility = get_realized_volatility(hist)

    first_open = float(hist['Open'].iloc[0])
    period_return_percent = ((last_close - first_open) / first_open * 100) if first_open else None

    # Where the symbol settled inside its own range: 0 at the low, 100 at the high.
    span = float(high_price) - float(low_price)
    close_in_range_percent = ((last_close - float(low_price)) / span * 100) if span else None

    return {
        "symbol": ticker,
        "high_price": round(float(high_price), 2),
        "high_when": high_idx.strftime("%Y-%m-%d %H:%M"),
        "low_price": round(float(low_price), 2),
        "low_when": low_idx.strftime("%Y-%m-%d %H:%M"),
        "change": round(change, 2),
        "change_percent": round(change_percent, 2),
        "change_days": change_days,
        "implied_volatility": implied_volatility,
        "expected_move_percent": round_or_none(expected_move_percent),
        "realized_vs_implied": round_or_none(realized_vs_implied),
        "realized_volatility": realized_volatility,
        "period_return_percent": round_or_none(period_return_percent),
        "close_in_range_percent": round_or_none(close_in_range_percent),
    }


def main():
    query, start_date, end_date, tickers_path = parse_args(sys.argv)
    tickers = read_tickers(tickers_path)

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
        "change_percent",
        "change_days",
        "implied_volatility",
        "expected_move_percent",
        "realized_vs_implied",
        "realized_volatility",
        "period_return_percent",
        "close_in_range_percent",
        "start_date",
        "end_date",
    ]

    print(
        f"[{datetime.now()}] Fetching hourly data ({period_desc}) for {len(tickers)} symbols "
        f"from {os.path.basename(tickers_path)}..."
    )
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
                row["change_percent"],
                row["change_days"],
                csv_value(row["implied_volatility"]),
                csv_value(row["expected_move_percent"]),
                csv_value(row["realized_vs_implied"]),
                csv_value(row["realized_volatility"]),
                csv_value(row["period_return_percent"]),
                csv_value(row["close_in_range_percent"]),
                start_label,
                end_label,
            ])
            iv_desc = "n/a" if row["implied_volatility"] is None else (
                f"{row['implied_volatility']}% (expected {row['expected_move_percent']}%, "
                f"{row['realized_vs_implied']}x)"
            )
            print(
                f"  {ticker}: high {row['high_price']} @ {row['high_when']}, "
                f"low {row['low_price']} @ {row['low_when']}, "
                f"change {row['change']} ({row['change_percent']}%) over {row['change_days']}d"
            )
            print(
                f"      IV {iv_desc}, RV {row['realized_volatility']}%, "
                f"return {row['period_return_percent']}%, "
                f"close {row['close_in_range_percent']}% of range"
            )

    print(f"[{datetime.now()}] Wrote output to {output_path}")


if __name__ == "__main__":
    main()
