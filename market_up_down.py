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
# Output: config/output/market-up-down-YYYYMMDDHHMM.csv and the same-named .html
# (a sortable report with the identical data, plus a link to the analysis page).
# CSV fields: symbol, high price, high date and hour, low price, low date and hour,
#             change (high - low, signed: positive when the low came first and the
#             symbol rose, negative when the high came first and the symbol fell),
#             change_median (change / 2), change_percent (change over the starting
#             price -- the low for a rise, the high for a fall),
#             last_close (previous market close), last_hour (price at the most
#             recent hourly bar), current_price (the price at the time the script
#             was run), change_from_last_close / change_from_last_close_percent
#             (current_price vs. last_close, in dollars and percent),
#             change_from_last_hour / change_from_last_hour_percent (current_price
#             vs. last_hour, in dollars and percent),
#             change_pct_from_high (current_price vs. high_price, percent; negative
#             is a fall from the high, positive a rise above it),
#             change_pct_from_low (current_price vs. low_price, percent; negative is
#             a further fall below the low, positive a recovery above it),
#             change_median_price ((low_price + high_price) / 2),
#             change_pct_from_change_median_price (current_price vs.
#             change_median_price, signed percent),
#             change_days (calendar days spanned, counting both the high and low
#             dates), implied_volatility (today's at-the-money option IV in percent;
#             blank when the symbol has no listed options), expected_move_percent
#             (IV rescaled to the change_days window), realized_vs_implied (how many
#             times larger the actual move was), realized_volatility (annualized,
#             from the hourly bars), period_return_percent (first open to last
#             close), close_in_range_percent (0 = closed at the low, 100 = at the
#             high)
import os
import sys
import csv
import html
import math
import yfinance as yf
from datetime import datetime, timedelta
from dotenv import load_dotenv

load_dotenv()

DEFAULT_PERIOD = "1"

# Column order for both the CSV and HTML reports: (field key, HTML header label,
# whether the column can be sorted in the HTML report, cell type for sorting).
# A "\n" in the label breaks the HTML header onto a second line so the column
# doesn't have to stretch to fit the whole phrase on one line.
# start_date and end_date are the same for every row in a report, so sorting by
# them is meaningless -- they're the only two columns excluded from sorting.
REPORT_COLUMNS = [
    ("symbol", "Symbol", True, "text"),
    ("high_price", "High\nPrice", True, "num"),
    ("high_when", "High\nDate/Hour", True, "text"),
    ("low_price", "Low\nPrice", True, "num"),
    ("low_when", "Low\nDate/Hour", True, "text"),
    ("change", "Change", True, "num"),
    ("change_median", "Change\n(Median)", True, "num"),
    ("change_percent", "Change %", True, "num"),
    ("last_close", "Last\nClose", True, "num"),
    ("last_hour", "Last\nHour", True, "num"),
    ("current_price", "Current\nPrice", True, "num"),
    ("change_from_last_close", "Change from\nLast Close", True, "toggle"),
    ("change_from_last_hour", "Change from\nLast Hour", True, "toggle"),
    ("change_pct_from_high", "% From\nHigh", True, "num"),
    ("change_pct_from_low", "% From\nLow", True, "num"),
    ("change_median_price", "Median\nPrice", True, "num"),
    ("change_pct_from_change_median_price", "% From\nMedian", True, "num"),
    ("change_days", "Days", True, "num"),
    ("implied_volatility", "Implied\nVol %", True, "num"),
    ("expected_move_percent", "Expected\nMove %", True, "num"),
    ("realized_vs_implied", "Realized/\nImplied", True, "num"),
    ("realized_volatility", "Realized\nVol %", True, "num"),
    ("period_return_percent", "Period\nReturn %", True, "num"),
    ("close_in_range_percent", "Close in\nRange %", True, "num"),
    ("start_date", "Start\nDate", False, "text"),
    ("end_date", "End\nDate", False, "text"),
]

# Columns whose sign carries meaning worth calling out with color (gains in
# green, losses in red) so the report reads at a glance.
SIGNED_COLUMNS = {
    "change",
    "change_median",
    "change_percent",
    "change_from_last_close",
    "change_from_last_hour",
    "change_pct_from_high",
    "change_pct_from_low",
    "change_pct_from_change_median_price",
    "period_return_percent",
}

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

    `last_close` is the previous market close (Yahoo's `previousClose`);
    `last_hour` is the close of the most recent hourly bar; `current_price` is
    the live price at the time the script is run (falling back to `last_hour`
    if a live quote isn't available). `change_from_last_close` /
    `change_from_last_close_percent` and `change_from_last_hour` /
    `change_from_last_hour_percent` measure `current_price` against those two
    reference points, in dollars and percent. `change` is the high-low spread
    signed by trend: positive when the low came first and the symbol rose to
    its high, negative when the high came first and the symbol fell to its
    low. `change_median` is change / 2. `change_percent` expresses that move
    against the price it started from -- the low for a rise, the high for a
    fall. `change_pct_from_high` and `change_pct_from_low` measure
    `current_price` against `high_price` and `low_price`, signed (negative for a
    fall, positive for a rise/recovery). `change_median_price` is the midpoint
    of the low and high, and `change_pct_from_change_median_price` measures
    `current_price` against that midpoint, signed. `change_days` is the calendar
    span the move covered, counting both the high and low dates (1 when they
    fall on the same day).

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

    last_hour_price = float(hist['Close'].iloc[-1])

    # Live price as of right now; falls back to the last available hourly close
    # if the live quote can't be fetched.
    try:
        current_price = float(stock.fast_info["lastPrice"])
    except Exception:
        current_price = last_hour_price

    # Previous official market close, separate from last_hour_price (which is
    # just the most recent bar in the requested period).
    try:
        last_close = float(stock.fast_info["previousClose"])
    except Exception:
        last_close = None

    change_from_last_close = round(current_price - last_close, 2) if last_close is not None else None
    change_from_last_close_percent = (
        round((current_price - last_close) / last_close * 100, 2)
        if last_close else None
    )
    change_from_last_hour = round(current_price - last_hour_price, 2)
    change_from_last_hour_percent = (
        round((current_price - last_hour_price) / last_hour_price * 100, 2)
        if last_hour_price else None
    )

    change_median = change / 2
    change_median_price = (float(low_price) + float(high_price)) / 2

    change_pct_from_high = (
        (current_price - float(high_price)) / float(high_price) * 100
    ) if high_price else 0.0
    change_pct_from_low = (
        (current_price - float(low_price)) / float(low_price) * 100
    ) if low_price else 0.0
    change_pct_from_change_median_price = (
        (current_price - change_median_price) / change_median_price * 100
    ) if change_median_price else 0.0

    # Options are a separate request and not every symbol has them; a failure
    # here should not cost us the price row.
    try:
        implied_volatility = get_implied_volatility(stock, last_hour_price)
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
    period_return_percent = ((last_hour_price - first_open) / first_open * 100) if first_open else None

    # Where the symbol settled inside its own range: 0 at the low, 100 at the high.
    span = float(high_price) - float(low_price)
    close_in_range_percent = ((last_hour_price - float(low_price)) / span * 100) if span else None

    return {
        "symbol": ticker,
        "high_price": round(float(high_price), 2),
        "high_when": high_idx.strftime("%Y-%m-%d %H:%M"),
        "low_price": round(float(low_price), 2),
        "low_when": low_idx.strftime("%Y-%m-%d %H:%M"),
        "change": round(change, 2),
        "change_median": round(change_median, 2),
        "change_percent": round(change_percent, 2),
        "last_close": round_or_none(last_close),
        "last_hour": round(last_hour_price, 2),
        "current_price": round(current_price, 2),
        "change_from_last_close": change_from_last_close,
        "change_from_last_close_percent": change_from_last_close_percent,
        "change_from_last_hour": change_from_last_hour,
        "change_from_last_hour_percent": change_from_last_hour_percent,
        "change_pct_from_high": round(change_pct_from_high, 2),
        "change_pct_from_low": round(change_pct_from_low, 2),
        "change_median_price": round(change_median_price, 2),
        "change_pct_from_change_median_price": round(change_pct_from_change_median_price, 2),
        "change_days": change_days,
        "implied_volatility": implied_volatility,
        "expected_move_percent": round_or_none(expected_move_percent),
        "realized_vs_implied": round_or_none(realized_vs_implied),
        "realized_volatility": realized_volatility,
        "period_return_percent": round_or_none(period_return_percent),
        "close_in_range_percent": round_or_none(close_in_range_percent),
    }


# Shared look for every report page (the up/down table and the analysis page),
# so the two feel like one report system. Plain string, not an f-string -- its
# braces are literal CSS and go in via substitution, not interpolation.
REPORT_CSS = """
  :root {
    --bg: #f5f7fa;
    --panel: #ffffff;
    --border: #e1e5ea;
    --text: #1c2530;
    --muted: #5b6673;
    --accent: #1a56c4;
    --accent-text: #ffffff;
    --pos: #0f7b3e;
    --neg: #c2261e;
    --header-bg: #10233f;
    --header-text: #eef2f8;
    --row-alt: #fafbfd;
    --row-hover: #eef3fb;
  }
  @media (prefers-color-scheme: dark) {
    :root:not([data-theme="light"]) {
      --bg: #0e1420;
      --panel: #171f2e;
      --border: #2b3648;
      --text: #e7ecf5;
      --muted: #97a3ba;
      --accent: #6fa2ff;
      --accent-text: #0e1420;
      --pos: #46d18a;
      --neg: #ff7a72;
      --row-alt: #1b2536;
      --row-hover: #223050;
    }
  }
  :root[data-theme="dark"] {
    --bg: #0e1420;
    --panel: #171f2e;
    --border: #2b3648;
    --text: #e7ecf5;
    --muted: #97a3ba;
    --accent: #6fa2ff;
    --accent-text: #0e1420;
    --pos: #46d18a;
    --neg: #ff7a72;
    --row-alt: #1b2536;
    --row-hover: #223050;
  }
  * { box-sizing: border-box; }
  body {
    margin: 0;
    padding: 32px 16px 64px;
    background: var(--bg);
    color: var(--text);
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
  }
  .wrap { max-width: 1400px; margin: 0 auto; }
  header.report-header {
    text-align: center;
    margin-bottom: 24px;
  }
  header.report-header h1 {
    margin: 0 0 6px;
    font-size: 1.9rem;
    letter-spacing: -0.01em;
  }
  header.report-header .subtitle {
    margin: 0 0 8px;
    font-size: 1.15rem;
    color: var(--accent);
    font-weight: 600;
  }
  header.report-header .meta {
    margin: 0;
    font-size: 0.9rem;
    color: var(--muted);
  }
  .panel {
    background: var(--panel);
    border: 1px solid var(--border);
    border-radius: 10px;
    overflow: hidden;
    box-shadow: 0 1px 3px rgba(16, 35, 63, 0.06);
  }
  .table-scroll {
    overflow-x: auto;
  }
  table {
    border-collapse: collapse;
    width: 100%;
    font-size: 0.86rem;
    white-space: nowrap;
  }
  thead th {
    position: sticky;
    top: 0;
    background: var(--header-bg);
    color: var(--header-text);
    text-align: right;
    padding: 0;
    font-weight: 600;
    border-bottom: 1px solid var(--border);
    z-index: 1;
  }
  thead th:first-child { text-align: left; }
  thead th.two-line { text-align: center; }
  thead th.unsortable {
    padding: 10px 12px;
    text-align: center;
    color: #b7c2d6;
    line-height: 1.25;
  }
  .sort-btn {
    all: unset;
    box-sizing: border-box;
    display: block;
    width: 100%;
    padding: 10px 12px;
    cursor: pointer;
    text-align: inherit;
    line-height: 1.25;
  }
  .sort-btn:hover { background: rgba(255, 255, 255, 0.08); }
  .sort-arrow { display: inline-block; width: 12px; margin-left: 2px; }
  .unit-toggle {
    display: inline-block;
    margin-left: 6px;
    padding: 1px 6px;
    border: 1px solid rgba(255, 255, 255, 0.35);
    border-radius: 4px;
    font-size: 0.72rem;
    font-weight: 500;
    cursor: pointer;
  }
  .unit-toggle:hover { background: rgba(255, 255, 255, 0.14); }
  tbody td {
    padding: 8px 12px;
    text-align: right;
    border-bottom: 1px solid var(--border);
    font-variant-numeric: tabular-nums;
  }
  tbody td:first-child { text-align: left; font-weight: 600; }
  tbody tr:nth-child(even) { background: var(--row-alt); }
  tbody tr:hover { background: var(--row-hover); }
  td.pos { color: var(--pos); font-weight: 600; }
  td.neg { color: var(--neg); font-weight: 600; }
  .analysis-link {
    text-align: center;
    margin: 28px 0;
  }
  .analysis-link a {
    display: inline-block;
    padding: 12px 28px;
    background: var(--accent);
    color: var(--accent-text);
    text-decoration: none;
    border-radius: 8px;
    font-weight: 600;
    font-size: 0.95rem;
  }
  .analysis-link a:hover { opacity: 0.88; }
  footer {
    text-align: center;
    margin-top: 32px;
    font-size: 0.85rem;
    color: var(--muted);
    line-height: 1.6;
  }
  footer a {
    color: var(--accent);
    text-decoration: none;
  }
  footer a:hover { text-decoration: underline; }
  .hint {
    text-align: center;
    color: var(--muted);
    font-size: 0.82rem;
    margin: 0 0 12px;
  }
"""


def format_header_label(label):
    """Escape a header label and turn its "\\n" marker into a <br> line break."""
    return "<br>".join(html.escape(part) for part in label.split("\n"))


def format_dollar(value):
    """Render a toggle-column value as a signed dollar amount, e.g. "-$3.00"."""
    if value is None:
        return ""
    sign = "-" if value < 0 else ""
    return f"{sign}${abs(value):.2f}"


def format_percent(value):
    """Render a toggle-column value as a percent, e.g. "2.86%"."""
    return "" if value is None else f"{value:.2f}%"


def render_footer_html():
    """Footer shared by every report page: created date, publisher link, copyright."""
    today_label = datetime.now().strftime("%B %d, %Y")
    current_year = datetime.now().year
    return (
        "  <footer>\n"
        f"    <p>Created on {html.escape(today_label)}</p>\n"
        '    <p><a href="https://datacommlab.com" target="_blank" rel="noopener">'
        f"Data Communications Lab</a> &copy; {current_year}</p>\n"
        "  </footer>"
    )


def render_html_report(
    rows, start_label, end_label, generated_at, symbol_count, tickers_name,
    period_desc, analysis_href,
):
    """Render the sortable HTML report as a single self-contained page.

    `rows` are the per-symbol dicts returned by get_high_low, in the order they
    should appear. Every CSV field is shown; start_date/end_date are filled in
    from start_label/end_label since they aren't part of the row dict.
    """
    header_cells = []
    for key, label, sortable, cell_type in REPORT_COLUMNS:
        line_class = " two-line" if "\n" in label else ""
        if sortable:
            sort_type = "num" if cell_type == "toggle" else cell_type
            toggle_html = (
                f'<span class="unit-toggle" data-target="{key}" data-unit="dollar" '
                'title="Click to toggle $ / %"><strong>&#36;</strong> / %</span>'
                if cell_type == "toggle" else ""
            )
            header_cells.append(
                f'<th data-type="{sort_type}" class="{line_class.strip()}"><button type="button" class="sort-btn">'
                f'{format_header_label(label)}{toggle_html}<span class="sort-arrow"></span></button></th>'
            )
        else:
            header_cells.append(f'<th class="unsortable{line_class}">{format_header_label(label)}</th>')
    header_html = "\n          ".join(header_cells)

    body_rows = []
    for row in rows:
        data = dict(row)
        data["start_date"] = start_label
        data["end_date"] = end_label
        cells = []
        for key, _label, _sortable, cell_type in REPORT_COLUMNS:
            value = data.get(key)
            text = "" if value is None else str(value)
            css_class = ""
            if key in SIGNED_COLUMNS and value is not None:
                css_class = ' class="pos"' if value > 0 else (' class="neg"' if value < 0 else "")
            if cell_type == "toggle":
                percent_value = data.get(key + "_percent")
                dollar_attr = "" if value is None else str(value)
                percent_attr = "" if percent_value is None else str(percent_value)
                display_dollar = format_dollar(value)
                display_percent = format_percent(percent_value)
                cells.append(
                    f'<td data-toggle-group="{key}" data-dollar="{dollar_attr}" '
                    f'data-percent="{percent_attr}" data-display-dollar="{html.escape(display_dollar)}" '
                    f'data-display-percent="{html.escape(display_percent)}" data-value="{dollar_attr}"'
                    f'{css_class}>{html.escape(display_dollar)}</td>'
                )
            elif cell_type == "num":
                data_value = "" if value is None else str(value)
                cells.append(f'<td data-value="{data_value}"{css_class}>{html.escape(text)}</td>')
            else:
                cells.append(f'<td{css_class}>{html.escape(text)}</td>')
        body_rows.append(f"        <tr>\n          " + "\n          ".join(cells) + "\n        </tr>")
    body_html = "\n".join(body_rows)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Blue Sky Stock Volatility Report</title>
<style>{REPORT_CSS}</style>
</head>
<body>
<div class="wrap">
  <header class="report-header">
    <h1>Blue Sky Stock Volatility Report</h1>
    <p class="subtitle">{html.escape(start_label)} &ndash; {html.escape(end_label)}</p>
    <p class="meta">Generated {html.escape(generated_at)} &middot; {symbol_count} symbols from {html.escape(tickers_name)} &middot; {html.escape(period_desc)}</p>
  </header>

  <p class="hint">Click a column header to sort; click again to reverse. Start Date and End Date are fixed for this report and aren't sortable.</p>

  <div class="panel">
    <div class="table-scroll">
      <table>
        <thead>
        <tr>
          {header_html}
        </tr>
        </thead>
        <tbody>
{body_html}
        </tbody>
      </table>
    </div>
  </div>

  <div class="analysis-link">
    <a href="{html.escape(analysis_href)}">Click here to read report analysis &rarr;</a>
  </div>

{render_footer_html()}
</div>

<script>
(function () {{
  document.querySelectorAll(".unit-toggle").forEach(function (toggle) {{
    toggle.addEventListener("click", function (e) {{
      e.stopPropagation();
      var target = toggle.dataset.target;
      var showPercent = toggle.dataset.unit !== "percent";
      toggle.dataset.unit = showPercent ? "percent" : "dollar";
      toggle.innerHTML = showPercent
        ? "$ / <strong>%</strong>"
        : "<strong>&#36;</strong> / %";
      document.querySelectorAll('td[data-toggle-group="' + target + '"]').forEach(function (td) {{
        td.dataset.value = showPercent ? td.dataset.percent : td.dataset.dollar;
        td.textContent = showPercent ? td.dataset.displayPercent : td.dataset.displayDollar;
      }});
    }});
  }});

  document.querySelectorAll(".sort-btn").forEach(function (btn) {{
    btn.addEventListener("click", function () {{
      var th = btn.closest("th");
      var table = th.closest("table");
      var tbody = table.tBodies[0];
      var index = Array.prototype.indexOf.call(th.parentNode.children, th);
      var type = th.dataset.type;
      var nextDir = th.dataset.dir === "asc" ? "desc" : "asc";

      table.querySelectorAll("th[data-type]").forEach(function (h) {{
        delete h.dataset.dir;
        var arrow = h.querySelector(".sort-arrow");
        if (arrow) arrow.textContent = "";
      }});
      th.dataset.dir = nextDir;
      btn.querySelector(".sort-arrow").textContent = nextDir === "asc" ? " \\u25B2" : " \\u25BC";

      var rows = Array.prototype.slice.call(tbody.querySelectorAll("tr"));
      rows.sort(function (a, b) {{
        var cellA = a.children[index];
        var cellB = b.children[index];
        var va = type === "num" ? cellA.dataset.value : cellA.textContent.trim().toLowerCase();
        var vb = type === "num" ? cellB.dataset.value : cellB.textContent.trim().toLowerCase();
        var emptyA = va === "" || va === undefined;
        var emptyB = vb === "" || vb === undefined;
        if (emptyA && emptyB) return 0;
        if (emptyA) return 1;
        if (emptyB) return -1;
        var cmp;
        if (type === "num") {{
          cmp = parseFloat(va) - parseFloat(vb);
        }} else {{
          cmp = va < vb ? -1 : (va > vb ? 1 : 0);
        }}
        return nextDir === "asc" ? cmp : -cmp;
      }});
      rows.forEach(function (r) {{ tbody.appendChild(r); }});
    }});
  }});
}})();
</script>
</body>
</html>
"""


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
    html_output_path = os.path.join(output_dir, f"market-up-down-{timestamp}.html")
    analysis_filename = f"market-analysis-{timestamp}.html"

    header = [
        "symbol",
        "high_price",
        "high_date_hour",
        "low_price",
        "low_date_hour",
        "change",
        "change_median",
        "change_percent",
        "last_close",
        "last_hour",
        "current_price",
        "change_from_last_close",
        "change_from_last_close_percent",
        "change_from_last_hour",
        "change_from_last_hour_percent",
        "change_pct_from_high",
        "change_pct_from_low",
        "change_median_price",
        "change_pct_from_change_median_price",
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
    rows_data = []
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
                row["change_median"],
                row["change_percent"],
                csv_value(row["last_close"]),
                row["last_hour"],
                row["current_price"],
                csv_value(row["change_from_last_close"]),
                csv_value(row["change_from_last_close_percent"]),
                row["change_from_last_hour"],
                csv_value(row["change_from_last_hour_percent"]),
                row["change_pct_from_high"],
                row["change_pct_from_low"],
                row["change_median_price"],
                row["change_pct_from_change_median_price"],
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
            rows_data.append(row)
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
                f"      last close {csv_value(row['last_close'])}, last hour {row['last_hour']}, "
                f"current {row['current_price']} "
                f"(from last close {csv_value(row['change_from_last_close'])} "
                f"({csv_value(row['change_from_last_close_percent'])}%), "
                f"from last hour {row['change_from_last_hour']} "
                f"({csv_value(row['change_from_last_hour_percent'])}%))"
            )
            print(
                f"      from high {row['change_pct_from_high']}%, from low {row['change_pct_from_low']}%, "
                f"from median {row['change_pct_from_change_median_price']}%"
            )
            print(
                f"      IV {iv_desc}, RV {row['realized_volatility']}%, "
                f"return {row['period_return_percent']}%, "
                f"close {row['close_in_range_percent']}% of range"
            )

    html_report = render_html_report(
        rows_data,
        start_label,
        end_label,
        generated_at=datetime.now().strftime("%B %d, %Y %I:%M %p"),
        symbol_count=len(rows_data),
        tickers_name=os.path.basename(tickers_path),
        period_desc=period_desc,
        analysis_href=analysis_filename,
    )
    with open(html_output_path, "w") as f:
        f.write(html_report)

    print(f"[{datetime.now()}] Wrote output to {output_path}")
    print(f"[{datetime.now()}] Wrote output to {html_output_path}")


if __name__ == "__main__":
    main()
