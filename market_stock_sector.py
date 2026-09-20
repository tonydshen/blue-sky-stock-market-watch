# market_stock_sector.py
# Revision history
# Revised on 09/17/2026
#   Created a skeleton file
#   Added requirements for the script to do as follows:
#   Implemented the requirements
# Revised on 09/20/2026
#   Added -m/-o merge mode (Requirements 09/20/2026 below)
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
#   uv run market_stock_sector.py -m "tickers-sp500-it.txt,tickers-ai.txt" -o tickers-tech.txt
#                                                        # merge files into config/tickers/tickers-tech.txt
#
# The tickers file is rewritten in place. A bare-symbol line such as
#   AAPL
# becomes
#   AAPL|Apple Inc.|Information Technology|$4.92T|Mega
# Only bare-symbol lines are looked up and filled in; a line that already has
# "|" fields is kept exactly as is. So after a symbol is appended to an
# already-populated file, re-running the script fills in just that new line.
# Line order, blank lines and an optional leading sector title line (e.g.
# "Health Sector", see market_up_down.read_tickers) are preserved. A file with
# no bare-symbol lines is left untouched.
#
# Company name, sector and market cap come from Yahoo Finance (yfinance Ticker.info). Yahoo's
# sector names map one-to-one onto the 11 GICS sectors (GICS_SECTORS below), so
# the file carries the GICS name. A symbol Yahoo can't classify -- most ETFs,
# indexes and some thinly traded ADRs -- gets empty fields ("SYM||||") and a
# warning on stderr.
#
# Merge mode (-m) reads the listed files in order and writes one new file in
# config/tickers (-o, default merged-tickers.txt); the input files are not
# modified. Each ticker is kept once, at its first occurrence, with the line
# it first appeared on: a "|" line is copied as is, a bare symbol is looked up
# as above. The output is sorted by symbol. Sector title lines, "#" comment
# lines and blank lines from the inputs are dropped, and the output ends with
# a trailer such as
#   # merged from tickers-sp500-it.txt, tickers-ai.txt on 2026-09-20 14:30:00
# The output name may not be one of the input files.
# Requirements 09/20/2026
# 1. Add an optional argument -m to merge files. 
# 2. When -m is provided, a quoted string of tickers files is expected, delimited by commas. The files are read in order, and the output is written to a new file. 
#    Only bare-symbol lines are looked up and filled in; a line that already has "|" fields is kept exactly as is. So after a symbol is appended to an already-populated file, re-running the script fills in just that new line.
# 3. The output file name is specified by the -o argument, and must be a file name only, without a path. The output file is written to the config/tickers folder.
# 4. Append a line to the outfile precedded with # sign and a whitespace, indicating the source files and the date/time of the merge. For example:" merged from tickers-sp500-it.txt, tickers-sp500-fin.txt on 2026-09-20 14:30:00"
# 5. If the -o argument is not provided, the default output file name is "merged-tickers.txt" in the config/tickers folder.
# 6. The merged file should not have duplicate tickers. If a ticker appears in multiple input files, only the first occurrence is kept in the output file.
# 7. The merged output file is sorted by ticker symbol.
import os
import sys
import yfinance as yf
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

# Output file name for -m when -o is not given (in config/tickers).
DEFAULT_MERGE_FILE = "merged-tickers.txt"

USAGE = (
    "Usage: uv run market_stock_sector.py [-f <tickers file>]\n"
    "       uv run market_stock_sector.py -m <file1,file2,...> [-o <output file>]\n"
    "  -f, --file           optional; name of a tickers file in config/tickers,\n"
    "                       e.g. tickers-sp500-it.txt (file name only, no path).\n"
    "                       Defaults to the file named by TICKERS_FILE.\n"
    "  -m, --merge          optional; comma-separated names of tickers files in\n"
    "                       config/tickers to merge, in order, into a new file\n"
    "                       (quote the list: -m \"a.txt,b.txt\"). Not with -f.\n"
    "  -o, --output         optional, with -m; name of the merged file, written\n"
    "                       to config/tickers (file name only, no path).\n"
    f"                       Defaults to {DEFAULT_MERGE_FILE}.\n"
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
    """Return (file_name, merge_files, output_name) from the command line.

    file_name is the -f/--file value or None for the default. merge_files is
    the list of names from -m/--merge, or None when not merging; output_name
    is the -o/--output value or None. -f and -m are mutually exclusive and -o
    is only meaningful with -m.
    """
    file_name = None
    merge_files = None
    output_name = None
    args = argv[1:]
    i = 0
    while i < len(args):
        arg = args[i]
        if arg in ("-f", "--file", "-m", "--merge", "-o", "--output"):
            i += 1
            if i >= len(args):
                usage_error(f"{arg} requires a value")
            value = args[i]
            if arg in ("-f", "--file"):
                file_name = value
            elif arg in ("-m", "--merge"):
                merge_files = [name.strip() for name in value.split(",") if name.strip()]
                if not merge_files:
                    usage_error(f"{arg} requires at least one file name")
            else:
                output_name = value
        else:
            usage_error(f"unexpected argument '{arg}'")
        i += 1

    if merge_files is not None and file_name is not None:
        usage_error("-f and -m cannot be used together")
    if output_name is not None and merge_files is None:
        usage_error("-o requires -m")
    return file_name, merge_files, output_name


def tickers_folder():
    """Return the absolute path of the tickers folder (TICKERS_PATH)."""
    folder = os.getenv("TICKERS_PATH")
    if not folder:
        usage_error("TICKERS_PATH is not set in .env")
    return get_absolute_path(folder)


def check_file_name_only(file_name, what="tickers file"):
    """Exit with a usage error unless file_name is a bare name with no path."""
    if os.path.basename(file_name) != file_name:
        usage_error(f"{what} '{file_name}' must be a file name only, without a path")


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
        check_file_name_only(file_name)
        path = os.path.join(tickers_folder(), file_name)

    if not os.path.isfile(path):
        usage_error(f"tickers file not found: {path}")
    return path


def is_title_line(line):
    """True for a sector title line such as "Health Sector" -- the same rule as
    market_up_down.read_tickers: no "|", and not an all-uppercase, space-free
    token like a bare ticker symbol."""
    return "|" not in line and not (line.isupper() and " " not in line)


def is_comment_line(line):
    """True for a "#" line, such as the merge trailer written by -m."""
    return line.startswith("#")


def is_bare_symbol(text):
    """True for a line that is just a ticker symbol and needs a lookup; lines
    that already carry "|" fields, titles, comments and blanks are not."""
    return bool(text) and "|" not in text and not is_comment_line(text) and not is_title_line(text)


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


def fill_lines(lines):
    """Look up every bare-symbol line in `lines` and return
    (output_lines, unclassified): the lines with each bare symbol replaced by
    its populated "SYM|company|sector|cap|category" line (everything else is
    passed through unchanged), and the symbols Yahoo had no sector or market
    cap for. Each populated line is echoed to stdout."""
    output_lines = []
    unclassified = []
    for line in lines:
        text = line.strip()
        if not is_bare_symbol(text):
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
    return output_lines, unclassified


def report_unclassified(unclassified):
    if unclassified:
        print(
            f"[{datetime.now()}] No sector or market cap on Yahoo Finance for: "
            + ", ".join(unclassified),
            file=sys.stderr,
        )


def fill_file(file_name):
    """-f mode: populate the bare-symbol lines of one tickers file in place."""
    tickers_path = resolve_tickers_path(file_name)

    with open(tickers_path, "r") as f:
        lines = [line.rstrip("\n") for line in f]

    # Only bare-symbol lines need a lookup; lines that already carry "|"
    # fields (populated on an earlier run, or hand-edited) are kept as is.
    symbols = [line.strip() for line in lines if is_bare_symbol(line.strip())]
    if not symbols:
        print(
            f"[{datetime.now()}] {os.path.basename(tickers_path)} has no new "
            "symbols to populate; nothing to do."
        )
        return
    print(
        f"[{datetime.now()}] Looking up company, sector and market cap for {len(symbols)} new "
        f"symbol{'s' if len(symbols) != 1 else ''} in {os.path.basename(tickers_path)}..."
    )

    output_lines, unclassified = fill_lines(lines)

    with open(tickers_path, "w") as f:
        f.write("\n".join(output_lines) + "\n")

    report_unclassified(unclassified)
    print(f"[{datetime.now()}] Updated {tickers_path}")


def merge_files(file_names, output_name):
    """-m mode: merge tickers files, in order, into a new file in config/tickers.

    A ticker is kept once, at its first occurrence, with the line it first
    appeared on; bare symbols are then looked up and the result is sorted by
    symbol. Title, "#" and blank lines from the inputs are dropped and a
    "# merged from ..." trailer is added.
    """
    if output_name is None:
        output_name = DEFAULT_MERGE_FILE
    check_file_name_only(output_name, "output file")
    if output_name in file_names:
        usage_error(f"output file '{output_name}' is also an input file")
    output_path = os.path.join(tickers_folder(), output_name)

    # Resolve (and so validate) every input before reading any of them.
    input_paths = [resolve_tickers_path(name) for name in file_names]

    merged = []
    seen = set()
    duplicates = 0
    for path in input_paths:
        with open(path, "r") as f:
            for line in f:
                text = line.strip()
                if not text or is_comment_line(text) or is_title_line(text):
                    continue
                symbol = text.split("|", 1)[0].strip()
                if symbol in seen:
                    duplicates += 1
                    continue
                seen.add(symbol)
                merged.append(text)

    symbols = [text for text in merged if is_bare_symbol(text)]
    print(
        f"[{datetime.now()}] Merging {len(file_names)} file{'s' if len(file_names) != 1 else ''} "
        f"into {output_name}: {len(merged)} unique ticker{'s' if len(merged) != 1 else ''}, "
        f"{duplicates} duplicate{'s' if duplicates != 1 else ''} dropped, "
        f"{len(symbols)} to look up..."
    )

    # Sort by symbol; the lookups above don't change the leading symbol.
    merged.sort(key=lambda text: text.split("|", 1)[0].strip())

    output_lines, unclassified = fill_lines(merged)
    output_lines.append(
        f"# merged from {', '.join(file_names)} on {datetime.now():%Y-%m-%d %H:%M:%S}"
    )

    existed = os.path.isfile(output_path)
    with open(output_path, "w") as f:
        f.write("\n".join(output_lines) + "\n")

    report_unclassified(unclassified)
    print(f"[{datetime.now()}] {'Replaced' if existed else 'Wrote'} {output_path}")


def main():
    file_name, merge_list, output_name = parse_args(sys.argv)
    if merge_list is not None:
        merge_files(merge_list, output_name)
    else:
        fill_file(file_name)


if __name__ == "__main__":
    main()
