# market_stock_sector.py
# Revision history
# Revised on 09/17/2026
#   Created a skeleton file
#   Added requirements for the script to do as follows:
#   Implemented the requirements
# Requirements
# 1. Read input tickers from a file in config/tickers, defaulting to the file named by TICKERS_FILE in .env.
# 2. If the input file has only ticker symbols and is not vertical bar delimnited, add two more fields to the file, delimited by vertical bars.
# 3. Add company name, then GICS Sector description from yfinace after each ticker symbol
# 4. Add market cap after sector description
# 5. Add a new field for the market cap category (Mega, Large, Mid, Small, Micro) based on the market cap value.
#
# Usage:
#   uv run market_stock_sector.py                        # TICKERS_FILE (config/tickers/tickers.txt)
#   uv run market_stock_sector.py -f tickers-sp500-it.txt  # another file in config/tickers
#
# The tickers file is rewritten in place. A bare-symbol line such as
#   AAPL
# becomes
#   AAPL|Apple Inc.|Information Technology|$4.92T|Mega
# Line order, blank lines and an optional leading sector title line (e.g.
# "Health Sector", see market_up_down.read_tickers) are preserved. A file that
# already contains "|" is left untouched, so re-running the script is safe.
#
# Company name, sector and market cap come from Yahoo Finance (yfinance Ticker.info). Yahoo's
# sector names map one-to-one onto the 11 GICS sectors (GICS_SECTORS below), so
# the file carries the GICS name. A symbol Yahoo can't classify -- most ETFs,
# indexes and some thinly traded ADRs -- gets empty fields ("SYM||||") and a
# warning on stderr.
import os
import sys
import yfinance as yf
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

USAGE = (
    "Usage: uv run market_stock_sector.py [-f <tickers file>]\n"
    "  -f, --file           optional; name of a tickers file in config/tickers,\n"
    "                       e.g. tickers-sp500-it.txt (file name only, no path).\n"
    "                       Defaults to the file named by TICKERS_FILE.\n"
)

# Yahoo Finance sector name -> GICS sector name. Yahoo follows Morningstar's
# taxonomy, which has the same 11 sectors as GICS under slightly different
# names; anything not listed here is written as Yahoo reports it.
GICS_SECTORS = {
    "Technology": "Information Technology",
    "Healthcare": "Health Care",
    "Financial Services": "Financials",
    "Consumer Cyclical": "Consumer Discretionary",
    "Consumer Defensive": "Consumer Staples",
    "Basic Materials": "Materials",
    "Industrials": "Industrials",
    "Energy": "Energy",
    "Utilities": "Utilities",
    "Real Estate": "Real Estate",
    "Communication Services": "Communication Services",
}

# Market cap categories, largest first: (lower bound in dollars, label).
# Anything below the last bound is Micro.
MARKET_CAP_CATEGORIES = [
    (200e9, "Mega"),
    (10e9, "Large"),
    (2e9, "Mid"),
    (300e6, "Small"),
    (0, "Micro"),
]


# Helper: Get absolute path relative to the script location
def get_absolute_path(path):
    if path.startswith('.'):
        script_dir = os.path.dirname(os.path.abspath(__file__))
        return os.path.join(script_dir, path.lstrip('./'))
    return path


def usage_error(message):
    print(f"Error: {message}\n", file=sys.stderr)
    print(USAGE, file=sys.stderr)
    sys.exit(2)


def parse_args(argv):
    """Return the tickers file name from -f/--file, or None for the default."""
    file_name = None
    args = argv[1:]
    i = 0
    while i < len(args):
        arg = args[i]
        if arg in ("-f", "--file"):
            i += 1
            if i >= len(args):
                usage_error(f"{arg} requires a value")
            file_name = args[i]
        else:
            usage_error(f"unexpected argument '{arg}'")
        i += 1
    return file_name


def resolve_tickers_path(file_name):
    """Return the full path of the tickers file to read.

    The tickers folder is TICKERS_PATH (config/tickers). `file_name` is a bare
    file name within that folder; when it is None the TICKERS_FILE default
    (tickers.txt) is used.
    """
    if file_name is None:
        default = os.getenv("TICKERS_FILE")
        if not default:
            usage_error("TICKERS_FILE is not set in .env")
        path = get_absolute_path(default)
    else:
        if os.path.basename(file_name) != file_name:
            usage_error(f"'{file_name}' must be a file name only, without a path")
        folder = os.getenv("TICKERS_PATH")
        if not folder:
            usage_error("TICKERS_PATH is not set in .env")
        path = os.path.join(get_absolute_path(folder), file_name)

    if not os.path.isfile(path):
        usage_error(f"tickers file not found: {path}")
    return path


def is_title_line(line):
    """True for a sector title line such as "Health Sector" -- the same rule as
    market_up_down.read_tickers: no "|", and not an all-uppercase, space-free
    token like a bare ticker symbol."""
    return "|" not in line and not (line.isupper() and " " not in line)


def get_stock_profile(symbol):
    """Return (company_name, gics_sector, market_cap) for a symbol from Yahoo
    Finance.

    Any value is None when Yahoo has no figure. A failed lookup (unknown
    symbol, network error) is reported on stderr and returns all None.
    """
    try:
        info = yf.Ticker(symbol).info or {}
    except Exception as exc:
        print(f"  {symbol}: lookup failed ({exc})", file=sys.stderr)
        return None, None, None

    company = info.get("longName") or info.get("shortName")
    sector = info.get("sector")
    if sector:
        sector = GICS_SECTORS.get(sector, sector)
    market_cap = info.get("marketCap")
    return company or None, sector or None, (int(market_cap) if market_cap else None)


def market_cap_category(market_cap):
    """Bucket a market cap into Mega/Large/Mid/Small/Micro; None -> None."""
    if market_cap is None:
        return None
    for lower_bound, label in MARKET_CAP_CATEGORIES:
        if market_cap >= lower_bound:
            return label
    return None


def format_market_cap(market_cap):
    """Render a market cap compactly, e.g. 4918238773248 -> "$4.92T"."""
    if market_cap is None:
        return ""
    for threshold, suffix in ((1e12, "T"), (1e9, "B"), (1e6, "M"), (1e3, "K")):
        if market_cap >= threshold:
            return f"${market_cap / threshold:.2f}{suffix}"
    return f"${market_cap:,}"


def main():
    file_name = parse_args(sys.argv)
    tickers_path = resolve_tickers_path(file_name)

    with open(tickers_path, "r") as f:
        lines = [line.rstrip("\n") for line in f]

    if any("|" in line for line in lines):
        print(
            f"[{datetime.now()}] {os.path.basename(tickers_path)} is already "
            "vertical-bar delimited; nothing to do."
        )
        return

    symbols = [line.strip() for line in lines if line.strip() and not is_title_line(line.strip())]
    print(
        f"[{datetime.now()}] Looking up company, sector and market cap for {len(symbols)} symbols "
        f"in {os.path.basename(tickers_path)}..."
    )

    output_lines = []
    unclassified = []
    for line in lines:
        text = line.strip()
        if not text or is_title_line(text):
            output_lines.append(line)
            continue

        symbol = text
        company, sector, market_cap = get_stock_profile(symbol)
        category = market_cap_category(market_cap)
        if sector is None or market_cap is None:
            unclassified.append(symbol)
        output_lines.append(
            f"{symbol}|{company or ''}|{sector or ''}|{format_market_cap(market_cap)}|{category or ''}"
        )
        print(f"  {output_lines[-1]}")

    with open(tickers_path, "w") as f:
        f.write("\n".join(output_lines) + "\n")

    if unclassified:
        print(
            f"[{datetime.now()}] No sector or market cap on Yahoo Finance for: "
            + ", ".join(unclassified),
            file=sys.stderr,
        )
    print(f"[{datetime.now()}] Updated {tickers_path}")


if __name__ == "__main__":
    main()
