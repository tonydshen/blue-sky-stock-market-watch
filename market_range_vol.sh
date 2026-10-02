#!/bin/bash
# market_range_vol.sh
# Runs the volatility report pipeline end to end and publishes the result:
# generates the up/down report and both AI analysis pages, copies the HTML
# output where the web server serves it, and adds a link to the new report
# at the top of the site's links page.
#
# Usage: market_range_vol.sh [-p <period>] [-f <tickers file>] [-l <tickers list file>]
#                            [-s <report script>] [-u <users file>] [-m <mailer>]
#   -p  period, forwarded to the report script's -p. Defaults to a
#       weekday-incrementing value so the window widens over the week:
#       Mon=14, Tue=15, Wed=16, Thu=17, Fri=18, Sat=19, Sun=20.
#   -f  tickers file, forwarded to the report script's -f (default: tickers.txt)
#   -l  tickers list file: a text file with one tickers file name per line
#       (e.g. tickers-ai.txt, tickers-energy.txt, ...). The full pipeline
#       runs once per line. Mutually exclusive with -f.
#   -s  report script to run (default: market_up_down.py). Supported:
#         market_up_down.py          full report + both market_analysis.py pages
#         market_up_down_concise.py  concise report only (no market_analysis.py);
#                                    the new report link(s) are emailed to users
#   -u  users file, one email address per line, for the concise report email
#       (default: $USERS_PATH/users.txt, USERS_PATH from .env). A bare file
#       name resolves against $USERS_PATH.
#   -m  how the concise report email goes out:
#         sendmail (default)  the server's sendmail (postfix on the remote
#                             server), the same way welcome.php's mail() sends
#         no                  send nothing; log the email that would have gone
#                             out (for machines without a mailer, e.g. WSL)
#       Either way the email is recorded in config/log/market_range_vol.log.
# Revision history:
#   2026-08-17: Initial version
#   2026-09-30: Additional features:
#       - Add optional commland line argumment -s to define what the script to run. If not given, defaults to market_up_down.py. If given, validate if it is market_up_down_concise.py or rejects it. More scripts may be added in future so please handle it gracefully.
#       - If -s market_up_down_concise.py is given, make sure this script works with it as it does with market_up_down.py, inclduing update links file, but skipping running market_analysis.py.
#       - If -s market_up_down_concise.py is given, add a sendmail rountine to run to send out report link to users.
#       - Add optional commandline argument -u to define users file. If not given, defaults $USERS_PATH/users.txt, $USERS_PATH is defined in .env.
#       - Add optional commandline argument -m. If not given, defaults to what the remote server uses to send emails (postfix, see welcome.php). If -m no is given, skip email and write messages to a log file indicating what is done.
#     Implemented 2026-09-30.
#   2026-10-02: market_up_down_concise.py now names its reports
#       market-up-down-concise-YYYYMMDDHHMM-<tickers tag>.html (e.g. -energy), so
#       -l runs that finish in the same minute no longer overwrite each other.
#       The timestamp is read from the first 12 characters after the prefix.
#
set -e

# variables
SOURCE_DIR=/home/tshen/agents/blue-sky-stock-market-watch/config/output
TARGET_DIR=/var/www/html/booths/pages
WORK_DIR=/home/tshen/agents/blue-sky-stock-market-watch
LINKS_FILE=/var/www/html/booths/links/blue-sky-stock-market-watch.htm
LOG_FILE=$WORK_DIR/config/log/market_range_vol.log

# public base URL of the web server serving TARGET_DIR (as /booths/pages/...),
# used for the links in the email; override with SITE_URL in .env
SITE_URL=https://datacommlab.com
# sender, as in welcome.php on the remote server; override with MAIL_FROM in .env
MAIL_FROM=tshen@datacommlab.com

# `date +%u` is the ISO weekday: 1=Monday .. 7=Sunday, so this gives
# Mon=14, Tue=15, Wed=16, Thu=17, Fri=18, Sat=19, Sun=20.
PERIOD=$((13 + $(date +%u)))
TICKERS_FILE=tickers.txt
TICKERS_LIST_FILE=""
F_GIVEN=0
REPORT_SCRIPT=market_up_down.py
USERS_FILE=""
MAILER=sendmail

USAGE="Usage: $0 [-p <period>] [-f <tickers file>] [-l <tickers list file>] [-s <report script>] [-u <users file>] [-m sendmail|no]"

while getopts "p:f:l:s:u:m:" opt; do
    case "$opt" in
        p) PERIOD="$OPTARG" ;;
        f) TICKERS_FILE="$OPTARG"; F_GIVEN=1 ;;
        l) TICKERS_LIST_FILE="$OPTARG" ;;
        s) REPORT_SCRIPT="$OPTARG" ;;
        u) USERS_FILE="$OPTARG" ;;
        m) MAILER="$OPTARG" ;;
        *) echo "$USAGE" >&2; exit 2 ;;
    esac
done

if [ -n "$TICKERS_LIST_FILE" ] && [ "$F_GIVEN" -eq 1 ]; then
    echo "Error: -f and -l are mutually exclusive" >&2
    exit 2
fi

# Per-script settings. To support another report script, add a case here:
#   REPORT_PREFIX  its output file name prefix (<prefix>YYYYMMDDHHMM[-<tag>].html)
#   RUN_ANALYSIS   1 to run both market_analysis.py passes after it
#   SEND_EMAIL     1 to email the new report link(s) to the users file
#   LABEL_SUFFIX   appended to the link text on the links page
case "$REPORT_SCRIPT" in
    market_up_down.py)
        REPORT_PREFIX=market-up-down-
        RUN_ANALYSIS=1
        SEND_EMAIL=0
        LABEL_SUFFIX=""
        ;;
    market_up_down_concise.py)
        REPORT_PREFIX=market-up-down-concise-
        RUN_ANALYSIS=0
        SEND_EMAIL=1
        LABEL_SUFFIX=" (Concise)"
        ;;
    *)
        echo "Error: unsupported report script for -s: $REPORT_SCRIPT" >&2
        echo "Supported: market_up_down.py, market_up_down_concise.py" >&2
        exit 2
        ;;
esac

case "$MAILER" in
    sendmail|no) ;;
    *) echo "Error: unsupported mailer for -m: $MAILER (use sendmail or no)" >&2; exit 2 ;;
esac

if [ "$SEND_EMAIL" -eq 0 ]; then
    [ -n "$USERS_FILE" ] && echo "Warning: -u is ignored with -s $REPORT_SCRIPT (no email is sent)" >&2
fi

cd "$WORK_DIR"

# read a single KEY=value from .env without sourcing the whole file
env_value() {
    grep -E "^$1=" "$WORK_DIR/.env" 2>/dev/null | tail -n 1 | cut -d= -f2- | sed -E 's/^"(.*)"$/\1/'
}

log() {
    mkdir -p "$(dirname "$LOG_FILE")"
    printf '%s %s\n' "$(date +"%Y-%m-%d %H:%M:%S")" "$*" >> "$LOG_FILE"
}

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

# the email needs the users file and a mailer, so validate them up front
# rather than after the reports have been generated
if [ "$SEND_EMAIL" -eq 1 ]; then
    USERS_DIR=$(env_value USERS_PATH)
    USERS_DIR=${USERS_DIR:-$WORK_DIR/config/users}
    USERS_FILE=${USERS_FILE:-users.txt}
    if [ ! -f "$USERS_FILE" ] && [ -f "$USERS_DIR/$USERS_FILE" ]; then
        USERS_FILE="$USERS_DIR/$USERS_FILE"
    fi
    if [ ! -f "$USERS_FILE" ]; then
        echo "Error: users file not found: $USERS_FILE" >&2
        exit 1
    fi
    ENV_SITE_URL=$(env_value SITE_URL); SITE_URL=${ENV_SITE_URL:-$SITE_URL}
    ENV_MAIL_FROM=$(env_value MAIL_FROM); MAIL_FROM=${ENV_MAIL_FROM:-$MAIL_FROM}
    if [ "$MAILER" = sendmail ]; then
        # postfix installs its sendmail-compatible binary here; PHP's mail() uses it too
        SENDMAIL=$(command -v sendmail || true)
        [ -z "$SENDMAIL" ] && [ -x /usr/sbin/sendmail ] && SENDMAIL=/usr/sbin/sendmail
        if [ -z "$SENDMAIL" ]; then
            echo "Error: sendmail not found (postfix not installed?); use -m no to skip the email" >&2
            exit 1
        fi
    fi
fi

# "label|url" of every report published this run, for the email
PUBLISHED_REPORTS=()

run_pipeline() {
local TICKERS_FILE="$1"
uv run "$REPORT_SCRIPT" -p "$PERIOD" -f "$TICKERS_FILE"

if [ "$RUN_ANALYSIS" -eq 1 ]; then
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
fi
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

# the report this run just produced is the newest <prefix>YYYYMMDDHHMM.html in
# SOURCE_DIR (the [0-9] keeps market-up-down- from matching the concise files)
NEW_REPORT=$(ls -t "$SOURCE_DIR"/${REPORT_PREFIX}[0-9]*.html 2>/dev/null | head -n 1)
if [ -z "$NEW_REPORT" ]; then
    echo "Error: no ${REPORT_PREFIX}*.html file found in $SOURCE_DIR" >&2
    exit 1
fi
if [ ! -f "$LINKS_FILE" ]; then
    echo "Error: links file not found: $LINKS_FILE" >&2
    exit 1
fi

REPORT_NAME=$(basename "$NEW_REPORT")
TIMESTAMP=${REPORT_NAME#"$REPORT_PREFIX"}
# the concise report adds "-<tickers tag>" after the timestamp; keep just the
# 12 timestamp digits
TIMESTAMP=${TIMESTAMP:0:12}
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
# market_up_down_concise.py writes no sidecar, so for it the sector title is
# recovered from the page title instead: "Blue Sky <sector> Stock Volatility
# Report (Concise)" (see render_report_html in market_up_down_concise.py).
SECTOR_TITLE=""
SECTOR_TITLE_FILE="$SOURCE_DIR/${REPORT_PREFIX}${TIMESTAMP}.sector-title.txt"
if [ -s "$SECTOR_TITLE_FILE" ]; then
    SECTOR_TITLE=$(cat "$SECTOR_TITLE_FILE")
elif [ "$REPORT_SCRIPT" = market_up_down_concise.py ]; then
    SECTOR_TITLE=$(sed -nE 's#.*<title>Blue Sky (.+) Stock Volatility Report \(Concise\)</title>.*#\1#p' "$NEW_REPORT" \
        | head -n 1 | sed -e 's/&amp;/\&/g' -e "s/&#x27;/'/g" -e 's/&quot;/"/g')
fi
LINK_LABEL="US Stock Market Volatility Report${LABEL_SUFFIX}"
if [ -n "$SECTOR_TITLE" ]; then
    LINK_LABEL="US Stock Market ${SECTOR_TITLE} Volatility Report${LABEL_SUFFIX}"
fi

NEW_LINK_ROW="$(printf '\t    ')<tr><td><a href=\"/booths/pages/${REPORT_NAME}\">${LINK_LABEL}, ${LINK_DATE}</a></td></tr>"

# insert the new row right after the header row that closes with </tr>,
# i.e. above whatever links are already there
awk -v newrow="$NEW_LINK_ROW" '
    { print }
    /<th align="left">Stock Market Volat.*ity Reports<\/th>/ { in_header = 1 }
    in_header && /<\/tr>/ && !inserted { print newrow; inserted = 1; in_header = 0 }
' "$LINKS_FILE" > "$LINKS_FILE.tmp" && mv "$LINKS_FILE.tmp" "$LINKS_FILE"

PUBLISHED_REPORTS+=("${LINK_LABEL}, ${LINK_DATE}|${SITE_URL}/booths/pages/${REPORT_NAME}")
}

# Email the links to every report published this run as one message (so -l
# with several tickers files still sends a single email). Like welcome.php it
# goes From/Reply-To MAIL_FROM with the sender copied; the users are Bcc'd so
# they don't see each other's address (sendmail -t strips the Bcc header).
send_report_email() {
    [ "${#PUBLISHED_REPORTS[@]}" -eq 0 ] && return 0
    local subject="Blue Sky Stock Volatility Report (Concise), $(date +"%B %-d, %Y %H:%M")"
    local body entry user
    local recipients=()

    body="New Blue Sky stock volatility report(s) are available:"$'\n\n'
    for entry in "${PUBLISHED_REPORTS[@]}"; do
        body+="${entry%%|*}"$'\n'"${entry#*|}"$'\n\n'
    done
    body+="datacommlab.com"$'\n'

    while IFS= read -r user || [ -n "$user" ]; do
        user=$(printf '%s' "$user" | tr -d '\r' | xargs)
        [ -z "$user" ] && continue
        case "$user" in \#*) continue ;; esac
        if ! [[ "$user" =~ ^[^@[:space:]]+@[^@[:space:]]+\.[^@[:space:]]+$ ]]; then
            echo "Warning: skipping invalid email address in $USERS_FILE: $user" >&2
            log "skipped invalid address in $USERS_FILE: $user"
            continue
        fi
        recipients+=("$user")
    done < "$USERS_FILE"

    if [ "${#recipients[@]}" -eq 0 ]; then
        echo "Warning: no email addresses in $USERS_FILE; no email sent" >&2
        log "no email addresses in $USERS_FILE; no email sent"
        return 0
    fi

    local bcc
    bcc=$(IFS=,; echo "${recipients[*]}")

    if [ "$MAILER" = no ]; then
        log "-m no: email NOT sent. Would have sent to ${#recipients[@]} user(s) from $USERS_FILE: $bcc"
        log "  Subject: $subject"
        while IFS= read -r entry; do log "  | $entry"; done <<< "$body"
        echo "Email skipped (-m no); see $LOG_FILE"
        return 0
    fi

    printf 'From: %s\nReply-To: %s\nTo: %s\nBcc: %s\nSubject: %s\nMIME-Version: 1.0\nContent-Type: text/plain; charset=UTF-8\n\n%s' \
        "$MAIL_FROM" "$MAIL_FROM" "$MAIL_FROM" "$bcc" "$subject" "$body" | "$SENDMAIL" -t
    log "sent via $SENDMAIL to ${#recipients[@]} user(s) from $USERS_FILE: $bcc"
    log "  Subject: $subject"
    echo "Report email sent to ${#recipients[@]} user(s) from $USERS_FILE"
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

if [ "$SEND_EMAIL" -eq 1 ]; then
    send_report_email
fi
