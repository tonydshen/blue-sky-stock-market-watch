#!/bin/bash
# market_range_vol.sh
# Runs the volatility report pipeline end to end and publishes the result:
# generates the up/down report and both AI analysis pages, copies the HTML
# output where the web server serves it, and adds a link to the new report
# at the top of the site's links page.
#
# Usage: market_range_vol.sh [-p <period>] [-f <tickers file>] [-l <tickers list file>]
#   -p  period, forwarded to market_up_down.py -p. Defaults to a
#       weekday-incrementing value so the window widens over the week:
#       Mon=14, Tue=15, Wed=16, Thu=17, Fri=18, Sat=19, Sun=20.
#   -f  tickers file, forwarded to market_up_down.py -f (default: tickers.txt)
#   -l  tickers list file: a text file with one tickers file name per line
#       (e.g. tickers-ai.txt, tickers-energy.txt, ...). The full pipeline
#       runs once per line. Mutually exclusive with -f.
set -e

# variables
SOURCE_DIR=/home/tshen/agents/blue-sky-stock-market-watch/config/output
TARGET_DIR=/var/www/html/booths/pages
WORK_DIR=/home/tshen/agents/blue-sky-stock-market-watch
LINKS_FILE=/var/www/html/booths/links/blue-sky-stock-market-watch.htm

# `date +%u` is the ISO weekday: 1=Monday .. 7=Sunday, so this gives
# Mon=14, Tue=15, Wed=16, Thu=17, Fri=18, Sat=19, Sun=20.
PERIOD=$((13 + $(date +%u)))
TICKERS_FILE=tickers.txt
TICKERS_LIST_FILE=""
F_GIVEN=0

while getopts "p:f:l:" opt; do
    case "$opt" in
        p) PERIOD="$OPTARG" ;;
        f) TICKERS_FILE="$OPTARG"; F_GIVEN=1 ;;
        l) TICKERS_LIST_FILE="$OPTARG" ;;
        *) echo "Usage: $0 [-p <period>] [-f <tickers file>] [-l <tickers list file>]" >&2; exit 2 ;;
    esac
done

if [ -n "$TICKERS_LIST_FILE" ] && [ "$F_GIVEN" -eq 1 ]; then
    echo "Error: -f and -l are mutually exclusive" >&2
    exit 2
fi

cd "$WORK_DIR"

# -l follows the same convention as -f: a bare file name resolves against the
# tickers config directory (matches TICKERS_PATH in .env), or an explicit
# relative/absolute path is used as given.
TICKERS_DIR="$WORK_DIR/config/tickers"
if [ -n "$TICKERS_LIST_FILE" ]; then
    if [ ! -f "$TICKERS_LIST_FILE" ] && [ -f "$TICKERS_DIR/$TICKERS_LIST_FILE" ]; then
        TICKERS_LIST_FILE="$TICKERS_DIR/$TICKERS_LIST_FILE"
    fi
    if [ ! -f "$TICKERS_LIST_FILE" ]; then
        echo "Error: tickers list file not found: $TICKERS_LIST_FILE" >&2
        exit 1
    fi
fi

run_pipeline() {
local TICKERS_FILE="$1"
uv run market_up_down.py -p "$PERIOD" -f "$TICKERS_FILE"

# When the tickers file has a sector title (see read_tickers in
# market_up_down.py), market_up_down.py writes a sector-focused macro prompt
# and drops its file name into this pointer file (cleared every run so a
# stale value never leaks into a run that didn't generate one). Forward it to
# both analysis passes so their "Broader Market Context" section is written
# through that sector's lens instead of the generic default.
SECTOR_PROMPT_POINTER="$SOURCE_DIR/.last-sector-prompt"
PROMPT_ARGS=()
if [ -s "$SECTOR_PROMPT_POINTER" ]; then
    PROMPT_ARGS=(--prompt-file "$(cat "$SECTOR_PROMPT_POINTER")")
fi

uv run market_analysis.py --model gemini-2.5-pro "${PROMPT_ARGS[@]}"
uv run market_analysis.py --model claude-opus-5 "${PROMPT_ARGS[@]}"
# copy html pages from source to target
cp "$SOURCE_DIR"/*.html "$TARGET_DIR"

# add link(s) for newly found html pages in TARGET_DIR to LINKS_FILE by
# inserting new links above the existing links
# Example:
#     <h2 align="left">Useful Links</h2>
#    <table>
#            <tr>
#                    <th align="left">Stock Market Volatility Reports</th>
#            </tr>
#            --> insert new links here
#            <tr><td><a href="/booths/pages/market-up-down-202608171321.html">US Stock Market Volatility Report, 13:21, August 17, 2026</a></td></tr>
#    </table>
#    <br>
#    <table>
#            <tr>

# the report this run just produced is the newest market-up-down-*.html in SOURCE_DIR
NEW_REPORT=$(ls -t "$SOURCE_DIR"/market-up-down-*.html 2>/dev/null | head -n 1)
if [ -z "$NEW_REPORT" ]; then
    echo "Error: no market-up-down-*.html file found in $SOURCE_DIR" >&2
    exit 1
fi
if [ ! -f "$LINKS_FILE" ]; then
    echo "Error: links file not found: $LINKS_FILE" >&2
    exit 1
fi

TIMESTAMP=$(basename "$NEW_REPORT" .html | sed -E 's/^market-up-down-//')
YEAR=${TIMESTAMP:0:4}
MONTH=${TIMESTAMP:4:2}
DAY=${TIMESTAMP:6:2}
HOUR=${TIMESTAMP:8:2}
MINUTE=${TIMESTAMP:10:2}
LINK_DATE=$(date -d "${YEAR}-${MONTH}-${DAY} ${HOUR}:${MINUTE}" +"%H:%M, %B %-d, %Y")

# market_up_down.py drops the tickers file's sector title (see
# write_sector_title_sidecar) here, keyed by this run's timestamp, when the
# tickers file had one -- e.g. "Energy Sector" for tickers-energy.txt. Work it
# into the link text the same way it's worked into the report titles.
LINK_LABEL="US Stock Market Volatility Report"
SECTOR_TITLE_FILE="$SOURCE_DIR/market-up-down-${TIMESTAMP}.sector-title.txt"
if [ -s "$SECTOR_TITLE_FILE" ]; then
    LINK_LABEL="US Stock Market $(cat "$SECTOR_TITLE_FILE") Volatility Report"
fi

NEW_LINK_ROW="$(printf '\t    ')<tr><td><a href=\"/booths/pages/market-up-down-${TIMESTAMP}.html\">${LINK_LABEL}, ${LINK_DATE}</a></td></tr>"

# insert the new row right after the header row that closes with </tr>,
# i.e. above whatever links are already there
awk -v newrow="$NEW_LINK_ROW" '
    { print }
    /<th align="left">Stock Market Volat.*ity Reports<\/th>/ { in_header = 1 }
    in_header && /<\/tr>/ && !inserted { print newrow; inserted = 1; in_header = 0 }
' "$LINKS_FILE" > "$LINKS_FILE.tmp" && mv "$LINKS_FILE.tmp" "$LINKS_FILE"
}

if [ -n "$TICKERS_LIST_FILE" ]; then
    while IFS= read -r line || [ -n "$line" ]; do
        # skip blank lines and comments
        [ -z "$line" ] && continue
        case "$line" in \#*) continue ;; esac
        run_pipeline "$line"
    done < "$TICKERS_LIST_FILE"
else
    run_pipeline "$TICKERS_FILE"
fi
