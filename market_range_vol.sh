#!/bin/bash
# market_range_vol.sh
# Runs the volatility report pipeline end to end and publishes the result:
# generates the up/down report and both AI analysis pages, copies the HTML
# output where the web server serves it, and adds a link to the new report
# at the top of the site's links page.
#
# Usage: market_range_vol.sh [-p <period>] [-f <tickers file>]
#   -p  period, forwarded to market_up_down.py -p (default: 14)
#   -f  tickers file, forwarded to market_up_down.py -f (default: tickers.txt)
set -e

# variables
SOURCE_DIR=/home/tshen/agents/blue-sky-stock-market-watch/config/output
TARGET_DIR=/var/www/html/booths/pages
WORK_DIR=/home/tshen/agents/blue-sky-stock-market-watch
LINKS_FILE=/var/www/html/booths/links/blue-sky-stock-market-watch.htm

PERIOD=14
TICKERS_FILE=tickers.txt

while getopts "p:f:" opt; do
    case "$opt" in
        p) PERIOD="$OPTARG" ;;
        f) TICKERS_FILE="$OPTARG" ;;
        *) echo "Usage: $0 [-p <period>] [-f <tickers file>]" >&2; exit 2 ;;
    esac
done

cd "$WORK_DIR"
uv run market_up_down.py -p "$PERIOD" -f "$TICKERS_FILE"
uv run market_analysis.py --model gemini-2.5-pro
uv run market_analysis.py --model claude-opus-5
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
NEW_LINK_ROW="$(printf '\t    ')<tr><td><a href=\"/booths/pages/market-up-down-${TIMESTAMP}.html\">US Stock Market Volatility Report, ${LINK_DATE}</a></td></tr>"

# insert the new row right after the header row that closes with </tr>,
# i.e. above whatever links are already there
awk -v newrow="$NEW_LINK_ROW" '
    { print }
    /<th align="left">Stock Market Volat.*ity Reports<\/th>/ { in_header = 1 }
    in_header && /<\/tr>/ && !inserted { print newrow; inserted = 1; in_header = 0 }
' "$LINKS_FILE" > "$LINKS_FILE.tmp" && mv "$LINKS_FILE.tmp" "$LINKS_FILE"
