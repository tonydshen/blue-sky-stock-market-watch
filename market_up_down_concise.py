# market_up_down_concise.py
# Note: This is a concise version of market_up_down.py, which is the main script for the Market Up/Down report. It has been stripped of the analysis section and other features that are not needed for the concise report. The output is a CSV and an HTML table with the same data, but without the analysis page.
# Concise version requirements, 09/20/2026:
# 1. To help the user quickly identify which direction each stock may go next. 
# 2. Add a visual indicator "Buy/Sell/Hold" based on ^VIX and the stock's implied volatility.
# 3. Simplify the report by including only the most relevant information as follows:
#    - Symbol
#    - Sector
#    - Market cap
#    - Size (Mega|large|Mid|Small"
#    - High Price
#    - High Date/Hour 
#    - Low Price
#    - Low Date/Hour
#    - Current Price
#    - Implied Vol
#    - Close in Range
#    - Expected Move
#    - Period Return
#    - Directional Indicator Up (green) or Down (red) or Flat (blue)
# 4. Keep each column sortable in the HTML report, with the exception of the Directional Indicator column.  
# 5. Keep everything else the same as the original report, including the ability to specify a tickers file and a date range or number of days.
# 6. Keep the same output file naming convention and location as the original report. Add suffix "-concise" to the file name, e.g. market-up-down-concise-YYYYMMDDHHMM.csv and market-up-down-concise-YYYYMMDDHHMM.html.
# 7. Keep the same command line interface as the original report, with the exception of the analysis section.
#
# Revised on 09/20/2026: implemented the requirements above.
# Revised on 09/20/2026 (later), after reviewing 1-day / 2-week / 1-month runs:
#   - dropped the "IV >= 4x VIX" veto (it held the high-IV memory/storage names
#     permanently at Hold regardless of trend) and the risk-on veto on Sell (a
#     calm VIX doesn't stop a single stock from falling); only risk-off vetoes Buy
#   - expected move now scales to the report period and falls back to realized
#     volatility from the hourly bars when a symbol has no options
#   - added the Days (change_days) column back after Low Date/Hour
# Revised on 09/20/2026 (later still), after a 75-symbol mixed-sector run:
#   - ETFs (and indexes, funds) are labeled in the Sector column from Yahoo's
#     quote type when the tickers file carries no sector, so they can be told
#     apart from stocks and grouped by sorting
#   - a stretched tag reads "Flat / Hold (ran up|down)" so a -25% Hold is
#     visibly "already ran", not a mistake; the CSV carries it as `stretched`
#   - Buy/Sell/Hold tallies in the page header and per sector under the table
#
# Reads live Yahoo Finance hourly data for the symbols in a tickers file and, for
# a user-defined period, finds each symbol's highest and lowest intraday price
# points (by hour), then tags each symbol with a direction (Up / Down / Flat) and
# the matching signal (Buy / Sell / Hold) derived from its price action, the
# ^VIX regime and its own implied volatility.
#
# Both arguments are optional:
#   - Last N days:      uv run market_up_down_concise.py -p 20
#   - Explicit range:   uv run market_up_down_concise.py -p 20260629-20260711
#     (June 29, 2026 through July 11, 2026, both inclusive)
#   - Tickers file:     uv run market_up_down_concise.py -f tickers-sp500-it.txt
#     (a file name only, no path; read from config/tickers)
#
# When -p is omitted the last 1 day is used; when -f is omitted the default file
# from TICKERS_FILE (config/tickers/tickers.txt) is used. With neither given:
#   uv run market_up_down_concise.py   # 1 day of data for config/tickers/tickers.txt
#
# Output: config/output/market-up-down-concise-YYYYMMDDHHMM.csv and the
# same-named .html (a sortable report with the identical data). Unlike
# market_up_down.py, no analysis link, sector prompt file, sector-title sidecar
# or .last-sector-prompt pointer is written -- those exist only to feed
# market_analysis.py, which the concise report doesn't have.
#
# Sector, market cap and size come from the tickers file's "|" fields as written
# by market_stock_sector.py ("AAPL|Apple Inc.|Information Technology|$4.92T|Mega").
# A bare-symbol line (no "|" fields) is looked up on Yahoo Finance instead; a
# line whose fields are present but empty (ETFs, indexes) is left blank.
#
# CSV fields: symbol, sector, market_cap (compact, e.g. $4.92T), size
#             (Mega/Large/Mid/Small/Micro), high price, high date and hour, low
#             price, low date and hour, change_days (calendar days spanned,
#             counting both the high and low dates), current_price (the price at
#             the time the script was run), implied_volatility (today's
#             at-the-money option IV in percent; blank when the symbol has no
#             listed options), close_in_range_percent (0 = closed at the low,
#             100 = at the high), expected_move_percent (IV rescaled to the
#             calendar days the period's bars span; from realized volatility of
#             the hourly bars when there is no IV), expected_move_basis (IV, RV
#             or blank), period_return_percent (first open to last close),
#             direction (Up/Down/Flat), signal (Buy/Sell/Hold), stretched ("ran
#             up" / "ran down" when the move already exceeded the expected move,
#             else blank), direction_reason (the inputs behind the tag),
#             start_date, end_date.
#
# Direction rule (see get_direction):
#   Trend      Up   when close_in_range >= TREND_UP_PCT and period_return > 0
#              Down when close_in_range <= TREND_DOWN_PCT and period_return < 0
#              Flat otherwise
#   Regime     from ^VIX now vs. its open at the start of the period:
#              risk-off when VIX >= VIX_FEAR_MIN or it rose >= VIX_RISING_PCT
#              risk-on  when VIX <  VIX_CALM_MAX and it did not rise that much
#              neutral  otherwise (or when ^VIX can't be fetched)
#              Only risk-off changes a tag (it vetoes Buy); risk-on and neutral
#              are shown for context.
#   Stretched  when the period return already ran past MOVE_VS_EXPECTED x the
#              expected move for the period (from the stock's IV, or its
#              realized volatility when it has no options)
#   Result     Up   / Buy  = trend Up, regime not risk-off, not stretched
#              Down / Sell = trend Down, not stretched
#              Flat / Hold = everything else
import os
import sys
import csv
import html
import math
import yfinance as yf
from datetime import datetime, timedelta
from dotenv import load_dotenv

from market_stock_sector import get_stock_profile, market_cap_category, format_market_cap

load_dotenv()

DEFAULT_PERIOD = "1"

# Column order for both the CSV and HTML reports: (field key, HTML header label,
# whether the column can be sorted in the HTML report, cell type for sorting).
# A "\n" in the label breaks the HTML header onto a second line so the column
# doesn't have to stretch to fit the whole phrase on one line.
# The direction column is the only unsortable one (requirement 4).
REPORT_COLUMNS = [
    ("symbol", "Symbol", True, "text"),
    ("sector", "Sector", True, "text"),
    ("market_cap", "Market\nCap", True, "num"),
    ("size", "Size", True, "num"),
    ("high_price", "High\nPrice", True, "num"),
    ("high_when", "High\nDate/Hour", True, "text"),
    ("low_price", "Low\nPrice", True, "num"),
    ("low_when", "Low\nDate/Hour", True, "text"),
    ("change_days", "Days", True, "num"),
    ("current_price", "Current\nPrice", True, "num"),
    ("implied_volatility", "Implied\nVol %", True, "num"),
    ("close_in_range_percent", "Close in\nRange %", True, "num"),
    ("expected_move_percent", "Expected\nMove %", True, "num"),
    ("period_return_percent", "Period\nReturn %", True, "num"),
    ("direction", "Direction\n(Signal)", False, "text"),
]

# Columns whose displayed text isn't what they sort by: the row key holding the
# numeric sort value (market cap in dollars; size as a rank, Mega highest).
SORT_VALUE_KEYS = {
    "market_cap": "market_cap_value",
    "size": "size_rank",
}

# Columns whose sign carries meaning worth calling out with color (gains in
# green, losses in red) so the report reads at a glance.
SIGNED_COLUMNS = {"period_return_percent"}

# Size labels in market_stock_sector order, ranked for sorting.
SIZE_RANKS = {"Mega": 5, "Large": 4, "Mid": 3, "Small": 2, "Micro": 1}

# Yahoo quote types that stand in for a sector when the tickers file has none
# (ETFs and the like have no GICS sector). Equities without a sector stay blank.
QUOTE_TYPE_LABELS = {"ETF": "ETF", "INDEX": "Index", "MUTUALFUND": "Fund"}

# Skip option expiries nearer than this when sampling implied volatility.
IV_MIN_DAYS = 7

# Hourly bars in a trading year: 6.5 session hours x 252 trading days. Used to
# annualize realized volatility so it can stand in for IV.
TRADING_HOURS_PER_YEAR = 6.5 * 252

MIN_BARS_FOR_VOLATILITY = 4

# Direction rule thresholds (see the header and get_direction).
VIX_SYMBOL = "^VIX"
VIX_CALM_MAX = 20.0      # VIX below this (and not rising) is risk-on
VIX_FEAR_MIN = 25.0      # VIX at or above this is risk-off
VIX_RISING_PCT = 10.0    # VIX up this much over the period is risk-off
TREND_UP_PCT = 70.0      # close_in_range at or above this reads as an uptrend
TREND_DOWN_PCT = 30.0    # close_in_range at or below this reads as a downtrend
MOVE_VS_EXPECTED = 1.5   # period return this far past the expected move = extended

# Direction -> (signal, arrow, CSS class).
DIRECTIONS = {
    "Up": ("Buy", "▲", "pos"),
    "Down": ("Sell", "▼", "neg"),
    "Flat": ("Hold", "►", "flat"),
}

USAGE = (
    "Usage: uv run market_up_down_concise.py [-f <tickers file>] [-p <period>]\n"
    "  -p, --period         optional; either:\n"
    "    N                    number of most recent days, e.g. 20\n"
    "    YYYYMMDD-YYYYMMDD    an explicit start-end date range (both inclusive),\n"
    "                         e.g. 20260629-20260711\n"
    f"                       Defaults to {DEFAULT_PERIOD} day.\n"
    "  -f, --file           optional; name of a tickers file in config/tickers,\n"
    "                       e.g. tickers-sp500-it.txt (file name only, no path).\n"
    "                       Defaults to the file named by TICKERS_FILE.\n"
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


def parse_market_cap(text):
    """Parse a compact market cap such as "$4.92T" back to dollars, or None."""
    if not text:
        return None
    text = text.strip().lstrip("$").replace(",", "")
    multipliers = {"T": 1e12, "B": 1e9, "M": 1e6, "K": 1e3}
    multiplier = multipliers.get(text[-1:].upper())
    if multiplier:
        text = text[:-1]
    try:
        return float(text) * (multiplier or 1)
    except ValueError:
        return None


def read_tickers(tickers_path):
    """Read a tickers file, returning (entries, sector_title).

    Each non-blank line is either a bare symbol ("AAPL") or a "|"-delimited
    record as written by market_stock_sector.py:
        SYMBOL|Company Name|GICS Sector|Market Cap|Size
    Each entry is a dict with symbol, sector, market_cap (compact text) and
    size; a bare symbol has "looked_up" False and None for the rest, so the
    caller can fetch them from Yahoo Finance. A file may optionally start with a
    plain title line (no "|"), e.g. "Health Sector", naming the sector it
    covers; when present it's returned as sector_title and excluded from the
    entries, otherwise sector_title is None. "#" lines are comments (e.g. the
    "# merged from ..." trailer written by market_stock_sector.py -m) and are
    skipped.
    """
    with open(get_absolute_path(tickers_path), "r") as f:
        lines = [line.strip() for line in f.readlines()
                 if line.strip() and not line.strip().startswith("#")]

    # A title line (e.g. "Health Sector") never contains "|" and doesn't look
    # like a bare ticker symbol (which is all-uppercase with no spaces, e.g.
    # "AAPL" or "BRK.B") -- that's what distinguishes it from the first
    # symbol of a plain tickers file.
    sector_title = None
    if lines and "|" not in lines[0] and not (lines[0].isupper() and " " not in lines[0]):
        sector_title = lines[0]
        lines = lines[1:]

    entries = []
    for line in lines:
        fields = [part.strip() for part in line.split("|")]
        symbol = fields[0]
        if len(fields) == 1:
            entries.append({"symbol": symbol, "sector": None, "market_cap": None,
                            "size": None, "looked_up": False})
            continue
        fields += [""] * (5 - len(fields))
        entries.append({
            "symbol": symbol,
            "sector": fields[2] or None,
            "market_cap": fields[3] or None,
            "size": fields[4] or None,
            "looked_up": True,
        })
    return entries, sector_title


def resolve_tickers_path(file_name):
    """Return the full path of the tickers file to read.

    The tickers folder is TICKERS_PATH (config/tickers). `file_name` is a bare
    file name within that folder; when it is None the TICKERS_FILE default
    (tickers.txt) is used.
    """
    if file_name is None:
        return get_absolute_path(os.getenv("TICKERS_FILE"))

    if os.path.basename(file_name) != file_name:
        usage_error(f"'{file_name}' must be a file name only, without a path")

    path = os.path.join(get_absolute_path(os.getenv("TICKERS_PATH")), file_name)
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


def get_vix(query):
    """Return (vix_now, vix_change_percent) for the ^VIX over the same period
    as the stocks: the live level and its change from the open of the first
    hourly bar in the period. Both are None when Yahoo has no data."""
    try:
        vix = yf.Ticker(VIX_SYMBOL)
        hist = vix.history(interval="1h", **query)
        if hist.empty:
            return None, None
        first_open = float(hist['Open'].iloc[0])
        try:
            now = float(vix.fast_info["lastPrice"])
        except Exception:
            now = float(hist['Close'].iloc[-1])
        change = ((now - first_open) / first_open * 100) if first_open else None
        return round(now, 2), round_or_none(change)
    except Exception as exc:
        print(f"  {VIX_SYMBOL}: error fetching data - {exc}", file=sys.stderr)
        return None, None


def vix_regime(vix, vix_change):
    """Classify the market regime from the ^VIX: "risk-off", "risk-on" or
    "neutral" (also when the VIX is unavailable)."""
    if vix is None:
        return "neutral"
    if vix >= VIX_FEAR_MIN or (vix_change is not None and vix_change >= VIX_RISING_PCT):
        return "risk-off"
    if vix < VIX_CALM_MAX:
        return "risk-on"
    return "neutral"


def get_direction(row, regime, vix):
    """Tag a symbol's next likely direction; returns (direction, signal, reason).

    Trend comes from the symbol's own price action (where it closed in its
    high-low range and the sign of its period return). An uptrend is vetoed
    by a risk-off ^VIX regime (an uptrend isn't a Buy when the market is in
    fear); a calm VIX says nothing about a single stock, so it doesn't veto a
    downtrend. Either trend is dropped to Flat/Hold when the period return has
    already run well past the expected move for the period -- from the
    symbol's implied volatility, or its realized volatility when it has no
    options -- since the move has already happened.
    """
    close_in_range = row["close_in_range_percent"]
    period_return = row["period_return_percent"]
    if close_in_range is None or period_return is None:
        trend = "Flat"
    elif close_in_range >= TREND_UP_PCT and period_return > 0:
        trend = "Up"
    elif close_in_range <= TREND_DOWN_PCT and period_return < 0:
        trend = "Down"
    else:
        trend = "Flat"

    stretched = None
    expected_move = row["expected_move_percent"]
    if expected_move and period_return is not None and abs(period_return) >= MOVE_VS_EXPECTED * expected_move:
        stretched = (
            f"return {period_return}% is {abs(period_return) / expected_move:.1f}x "
            f"the expected {expected_move}% for {row['period_days']}d "
            f"(from {'IV' if row['expected_move_basis'] == 'IV' else 'realized vol'} "
            f"{row['volatility_used']}%)"
        )

    if trend == "Up" and regime != "risk-off" and not stretched:
        direction = "Up"
    elif trend == "Down" and not stretched:
        direction = "Down"
    else:
        direction = "Flat"

    def na(value):
        return "n/a" if value is None else value

    reason = (
        f"trend {trend.lower()} (close {na(close_in_range)}% of range, "
        f"return {na(period_return)}%); "
        f"VIX {'n/a' if vix is None else vix} {regime}; "
        + (f"stretched: {stretched}" if stretched else
           f"expected {na(expected_move)}% for {row['period_days']}d "
           f"({'IV' if row['expected_move_basis'] == 'IV' else 'realized vol' if row['expected_move_basis'] else 'no vol data'}"
           f"{'' if row['volatility_used'] is None else ' ' + str(row['volatility_used']) + '%'}) not exceeded")
    )
    stretched_label = ""
    if stretched:
        stretched_label = "ran up" if period_return > 0 else "ran down"
    return direction, DIRECTIONS[direction][0], reason, stretched_label


def get_high_low(ticker, query):
    """Return the highest and lowest hourly price points for a ticker.

    `query` is the dict produced by parse_period (either a period or a
    start/end range). Uses hourly (1h) intraday bars. The high point is the max
    of the hourly High column; the low point is the min of the hourly Low
    column. Returns a dict of result fields, or None if no data is available.

    `current_price` is the live price at the time the script is run (falling
    back to the close of the most recent hourly bar if a live quote isn't
    available). `change_days` is the calendar span the high-low move covered,
    counting both the high and low dates; `period_days` is the span of all the
    bars. `expected_move_percent` is the annualized volatility rescaled to
    period_days -- from the implied volatility when the symbol has options,
    otherwise from the realized volatility of the bars (`expected_move_basis`
    is "IV" or "RV", `volatility_used` the figure it came from).
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

    # Inclusive calendar spans, so a move within a single day counts as 1.
    change_days = abs((high_idx.date() - low_idx.date()).days) + 1
    period_days = (hist.index[-1].date() - hist.index[0].date()).days + 1

    last_hour_price = float(hist['Close'].iloc[-1])

    # Live price as of right now; falls back to the last available hourly close
    # if the live quote can't be fetched.
    try:
        current_price = float(stock.fast_info["lastPrice"])
    except Exception:
        current_price = last_hour_price

    # Same metadata fetch as the live price; tells an ETF from a stock.
    try:
        quote_type = stock.fast_info["quoteType"]
    except Exception:
        quote_type = None

    # Options are a separate request and not every symbol has them; a failure
    # here should not cost us the price row.
    try:
        implied_volatility = get_implied_volatility(stock, last_hour_price)
    except Exception:
        implied_volatility = None

    # Volatility is quoted annualized; rescale it to the period so it can be
    # read against the period return. IV when the symbol has options, else the
    # realized volatility of the bars themselves.
    if implied_volatility:
        volatility_used, expected_move_basis = implied_volatility, "IV"
    else:
        volatility_used = get_realized_volatility(hist)
        expected_move_basis = "RV" if volatility_used else ""
    expected_move_percent = None
    if volatility_used:
        expected_move_percent = volatility_used * math.sqrt(period_days / 365)

    first_open = float(hist['Open'].iloc[0])
    period_return_percent = ((last_hour_price - first_open) / first_open * 100) if first_open else None

    # Where the symbol settled inside its own range: 0 at the low, 100 at the high.
    span = float(high_price) - float(low_price)
    close_in_range_percent = ((last_hour_price - float(low_price)) / span * 100) if span else None

    return {
        "symbol": ticker,
        "quote_type": quote_type,
        "high_price": round(float(high_price), 2),
        "high_when": high_idx.strftime("%Y-%m-%d %H:%M"),
        "low_price": round(float(low_price), 2),
        "low_when": low_idx.strftime("%Y-%m-%d %H:%M"),
        "current_price": round(current_price, 2),
        "change_days": change_days,
        "period_days": period_days,
        "implied_volatility": implied_volatility,
        "expected_move_percent": round_or_none(expected_move_percent),
        "expected_move_basis": expected_move_basis,
        "volatility_used": volatility_used,
        "period_return_percent": round_or_none(period_return_percent),
        "close_in_range_percent": round_or_none(close_in_range_percent),
    }


def add_profile(row, entry):
    """Fill the row's sector, market_cap (compact text), market_cap_value,
    size and size_rank from the tickers file entry, looking the symbol up on
    Yahoo Finance when the file had only the bare symbol."""
    sector, market_cap_text, size = entry["sector"], entry["market_cap"], entry["size"]
    if not entry["looked_up"]:
        company, sector, market_cap = get_stock_profile(entry["symbol"])
        market_cap_text = format_market_cap(market_cap) or None
        size = market_cap_category(market_cap)

    # An ETF/index/fund has no GICS sector; show what it is instead of a blank.
    if not sector and row.get("quote_type"):
        sector = QUOTE_TYPE_LABELS.get(str(row["quote_type"]).upper())

    market_cap_value = parse_market_cap(market_cap_text)
    row["sector"] = sector
    row["market_cap"] = market_cap_text
    row["market_cap_value"] = market_cap_value
    row["size"] = size
    row["size_rank"] = SIZE_RANKS.get(size)


# Shared look with the full report (market_up_down.py), so the two feel like
# one report system. Plain string, not an f-string -- its braces are literal
# CSS and go in via substitution, not interpolation.
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
    --flat: #1a56c4;
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
      --flat: #6fa2ff;
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
    --flat: #6fa2ff;
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
    /* Both axes are handled by this one scroll container (not split
       between it and an ancestor) so thead's `position: sticky` below
       sticks to *this* scrollport. An ancestor with any non-visible
       overflow -- even overflow-x: auto alone, since the browser then
       forces the other axis to compute as auto too -- would otherwise
       become the sticky containing block instead of the viewport, and
       since that ancestor never scrolls on its own, the header would
       silently fail to stick. */
    overflow: auto;
    max-height: 75vh;
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
  tbody td {
    padding: 8px 12px;
    text-align: right;
    border-bottom: 1px solid var(--border);
    font-variant-numeric: tabular-nums;
  }
  tbody td:first-child { text-align: left; font-weight: 600; }
  tbody td.text { text-align: left; }
  tbody tr:nth-child(even) { background: var(--row-alt); }
  tbody tr:hover { background: var(--row-hover); }
  td.pos { color: var(--pos); font-weight: 600; }
  td.neg { color: var(--neg); font-weight: 600; }
  td.flat { color: var(--flat); font-weight: 600; }
  td.direction { text-align: center; cursor: help; }
  header.report-header .tally {
    margin: 8px 0 0;
    font-size: 0.95rem;
  }
  .tally b.pos, .sector-tally b.pos { color: var(--pos); }
  .tally b.neg, .sector-tally b.neg { color: var(--neg); }
  .tally b.flat, .sector-tally b.flat { color: var(--flat); }
  .sector-tally {
    margin: 16px auto 0;
    max-width: 900px;
    font-size: 0.82rem;
    color: var(--muted);
  }
  .sector-tally p { margin: 0 0 4px; }
  .sector-tally table { width: auto; font-size: inherit; white-space: nowrap; }
  .sector-tally td { padding: 2px 14px 2px 0; text-align: left; }
  .sector-tally td:nth-child(2) { text-align: right; }
  .legend {
    margin: 16px auto 0;
    max-width: 900px;
    font-size: 0.82rem;
    color: var(--muted);
    line-height: 1.55;
  }
  .legend b.pos { color: var(--pos); }
  .legend b.neg { color: var(--neg); }
  .legend b.flat { color: var(--flat); }
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


def tally(rows):
    """Count rows per direction, in DIRECTIONS order: {"Up": n, "Down": n, "Flat": n}."""
    counts = {direction: 0 for direction in DIRECTIONS}
    for row in rows:
        counts[row["direction"]] += 1
    return counts


def format_tally_html(counts):
    """Render a tally as colored "▲ 9 Buy · ▼ 3 Sell · ► 11 Hold"."""
    parts = []
    for direction, (signal, arrow, css_class) in DIRECTIONS.items():
        parts.append(f'<b class="{css_class}">{arrow} {counts[direction]} {signal}</b>')
    return " &middot; ".join(parts)


def render_sector_tally_html(rows):
    """Per-sector Buy/Sell/Hold counts under the table; empty when the report
    covers a single sector (the header tally already says it all)."""
    sectors = {}
    for row in rows:
        sectors.setdefault(row["sector"] or "(no sector)", []).append(row)
    if len(sectors) < 2:
        return ""
    lines = []
    for sector in sorted(sectors, key=lambda name: (-len(sectors[name]), name)):
        lines.append(
            f"    <tr><td>{html.escape(sector)}</td><td>{len(sectors[sector])}</td>"
            f"<td>{format_tally_html(tally(sectors[sector]))}</td></tr>"
        )
    return (
        '  <div class="sector-tally">\n'
        "    <p><b>By sector</b></p>\n"
        "    <table>\n"
        + "\n".join(lines) + "\n"
        "    </table>\n"
        "  </div>"
    )


def render_legend_html(vix, vix_change, regime):
    """The direction rule, spelled out under the table, with the ^VIX reading
    that applied to this report."""
    if vix is None:
        vix_text = "^VIX unavailable for this run, so the regime was treated as neutral."
    else:
        change_text = "" if vix_change is None else f", {vix_change:+.2f}% over the period"
        vix_text = f"^VIX {vix:.2f}{change_text}: regime <b>{html.escape(regime)}</b>."
    return (
        '  <div class="legend">\n'
        f"    <p><b>Direction:</b> {vix_text}<br>\n"
        f"    <b class=\"pos\">{DIRECTIONS['Up'][1]} Up / Buy</b> &ndash; closed in the top "
        f"{100 - TREND_UP_PCT:.0f}% of its range with a positive period return, the market is not "
        f"risk-off (VIX &lt; {VIX_FEAR_MIN:.0f} and not up {VIX_RISING_PCT:.0f}%+), and the move isn't stretched.<br>\n"
        f"    <b class=\"neg\">{DIRECTIONS['Down'][1]} Down / Sell</b> &ndash; closed in the bottom "
        f"{TREND_DOWN_PCT:.0f}% of its range with a negative period return, and the move isn't stretched.<br>\n"
        f"    <b class=\"flat\">{DIRECTIONS['Flat'][1]} Flat / Hold</b> &ndash; everything else, including a "
        f"stretched move: a period return past {MOVE_VS_EXPECTED}x the Expected Move (the move has already run). "
        "Expected Move is the implied volatility scaled to this period, or the realized volatility of the "
        "hourly bars for a symbol with no options. Hover a tag for the reasoning.</p>\n"
        "  </div>"
    )


def render_html_report(
    rows, start_label, end_label, generated_at, symbol_count, tickers_name,
    period_desc, vix, vix_change, regime, sector_title=None,
):
    """Render the sortable HTML report as a single self-contained page.

    `rows` are the per-symbol dicts returned by get_high_low plus add_profile
    and get_direction, in the order they should appear.

    `sector_title`, when the tickers file named one (see read_tickers), is
    worked into the report title, e.g. "Blue Sky Health Sector Stock
    Volatility Report (Concise)".
    """
    report_title = (
        f"Blue Sky {sector_title} Stock Volatility Report (Concise)" if sector_title
        else "Blue Sky Stock Volatility Report (Concise)"
    )

    header_cells = []
    for key, label, sortable, cell_type in REPORT_COLUMNS:
        line_class = " two-line" if "\n" in label else ""
        if sortable:
            header_cells.append(
                f'<th data-type="{cell_type}" class="{line_class.strip()}"><button type="button" class="sort-btn">'
                f'{format_header_label(label)}<span class="sort-arrow"></span></button></th>'
            )
        else:
            header_cells.append(f'<th class="unsortable{line_class}">{format_header_label(label)}</th>')
    header_html = "\n          ".join(header_cells)

    body_rows = []
    for row in rows:
        cells = []
        for key, _label, _sortable, cell_type in REPORT_COLUMNS:
            value = row.get(key)
            text = "" if value is None else str(value)
            classes = []
            if key in SIGNED_COLUMNS and value is not None:
                classes.append("pos" if value > 0 else ("neg" if value < 0 else ""))
            if key == "direction":
                signal, arrow, css_class = DIRECTIONS[value]
                classes += ["direction", css_class]
                text = f"{arrow} {value} · {signal}"
                if row["stretched"]:
                    text += f" ({row['stretched']})"
                title = f' title="{html.escape(row["direction_reason"])}"'
                cells.append(f'<td class="{" ".join(classes)}"{title}>{html.escape(text)}</td>')
                continue
            if cell_type == "text" and key != "symbol":
                classes.append("text")
            class_attr = f' class="{" ".join(c for c in classes if c)}"' if any(classes) else ""
            if cell_type == "num":
                sort_value = row.get(SORT_VALUE_KEYS.get(key, key))
                data_value = "" if sort_value is None else str(sort_value)
                cells.append(f'<td data-value="{data_value}"{class_attr}>{html.escape(text)}</td>')
            else:
                cells.append(f'<td{class_attr}>{html.escape(text)}</td>')
        body_rows.append(f"        <tr>\n          " + "\n          ".join(cells) + "\n        </tr>")
    body_html = "\n".join(body_rows)

    if vix is None:
        vix_meta = "^VIX n/a"
    else:
        vix_meta = f"^VIX {vix:.2f}" + ("" if vix_change is None else f" ({vix_change:+.2f}%)") + f" {regime}"

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{html.escape(report_title)}</title>
<style>{REPORT_CSS}</style>
</head>
<body>
<div class="wrap">
  <header class="report-header">
    <h1>{html.escape(report_title)}</h1>
    <p class="subtitle">{html.escape(start_label)} &ndash; {html.escape(end_label)}</p>
    <p class="meta">Generated {html.escape(generated_at)} &middot; {symbol_count} symbols from {html.escape(tickers_name)} &middot; {html.escape(period_desc)} &middot; {html.escape(vix_meta)}</p>
    <p class="tally">{format_tally_html(tally(rows))}</p>
  </header>

  <p class="hint">Click a column header to sort; click again to reverse. The Direction column isn't sortable.</p>

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

{render_sector_tally_html(rows)}

{render_legend_html(vix, vix_change, regime)}

{render_footer_html()}
</div>

<script>
(function () {{
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
    entries, sector_title = read_tickers(tickers_path)

    start_label = start_date.strftime("%m/%d/%Y")
    end_label = end_date.strftime("%m/%d/%Y")

    if "period" in query:
        period_desc = f"last {query['period']} ({start_label} - {end_label})"
    else:
        period_desc = f"{start_label} through {end_label}"

    output_dir = os.getenv("OUTPUT_PATH")
    timestamp = datetime.now().strftime("%Y%m%d%H%M")
    output_path = os.path.join(output_dir, f"market-up-down-concise-{timestamp}.csv")
    html_output_path = os.path.join(output_dir, f"market-up-down-concise-{timestamp}.html")

    header = [
        "symbol",
        "sector",
        "market_cap",
        "size",
        "high_price",
        "high_date_hour",
        "low_price",
        "low_date_hour",
        "change_days",
        "current_price",
        "implied_volatility",
        "close_in_range_percent",
        "expected_move_percent",
        "expected_move_basis",
        "period_return_percent",
        "direction",
        "signal",
        "stretched",
        "direction_reason",
        "start_date",
        "end_date",
    ]

    # One ^VIX reading sets the regime for every symbol in the report.
    vix, vix_change = get_vix(query)
    regime = vix_regime(vix, vix_change)
    if vix is None:
        print(f"[{datetime.now()}] {VIX_SYMBOL} unavailable; regime treated as neutral")
    else:
        print(
            f"[{datetime.now()}] {VIX_SYMBOL} {vix} "
            f"({'n/a' if vix_change is None else f'{vix_change:+.2f}%'} over the period): {regime}"
        )

    print(
        f"[{datetime.now()}] Fetching hourly data ({period_desc}) for {len(entries)} symbols "
        f"from {os.path.basename(tickers_path)}..."
    )
    rows_data = []
    with open(output_path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(header)
        for entry in entries:
            ticker = entry["symbol"]
            try:
                row = get_high_low(ticker, query)
            except Exception as e:
                print(f"  {ticker}: error fetching data - {e}")
                continue
            if row is None:
                print(f"  {ticker}: no data available")
                continue
            add_profile(row, entry)
            row["direction"], row["signal"], row["direction_reason"], row["stretched"] = get_direction(row, regime, vix)
            writer.writerow([
                row["symbol"],
                csv_value(row["sector"]),
                csv_value(row["market_cap"]),
                csv_value(row["size"]),
                row["high_price"],
                row["high_when"],
                row["low_price"],
                row["low_when"],
                row["change_days"],
                row["current_price"],
                csv_value(row["implied_volatility"]),
                csv_value(row["close_in_range_percent"]),
                csv_value(row["expected_move_percent"]),
                row["expected_move_basis"],
                csv_value(row["period_return_percent"]),
                row["direction"],
                row["signal"],
                row["stretched"],
                row["direction_reason"],
                start_label,
                end_label,
            ])
            rows_data.append(row)
            iv_desc = "n/a" if row["implied_volatility"] is None else f"{row['implied_volatility']}%"
            if row["expected_move_percent"] is not None:
                iv_desc += f" (expected {row['expected_move_percent']}% from {row['expected_move_basis']})"
            print(
                f"  {ticker} [{csv_value(row['sector'])}, {csv_value(row['market_cap'])} {csv_value(row['size'])}]: "
                f"high {row['high_price']} @ {row['high_when']}, "
                f"low {row['low_price']} @ {row['low_when']} over {row['change_days']}d, "
                f"current {row['current_price']}, IV {iv_desc}, "
                f"return {row['period_return_percent']}%, "
                f"close {row['close_in_range_percent']}% of range "
                f"-> {row['direction']} / {row['signal']}"
                + (f" ({row['stretched']})" if row["stretched"] else "")
            )

    html_report = render_html_report(
        rows_data,
        start_label,
        end_label,
        generated_at=datetime.now().strftime("%B %d, %Y %I:%M %p"),
        symbol_count=len(rows_data),
        tickers_name=os.path.basename(tickers_path),
        period_desc=period_desc,
        vix=vix,
        vix_change=vix_change,
        regime=regime,
        sector_title=sector_title,
    )
    with open(html_output_path, "w") as f:
        f.write(html_report)

    counts = tally(rows_data)
    print(
        f"[{datetime.now()}] {len(rows_data)} symbols: "
        + ", ".join(f"{counts[d]} {DIRECTIONS[d][0]}" for d in DIRECTIONS)
    )
    print(f"[{datetime.now()}] Wrote output to {output_path}")
    print(f"[{datetime.now()}] Wrote output to {html_output_path}")


if __name__ == "__main__":
    main()
