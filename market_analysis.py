# market_analysis.py
# Generates the AI-written analysis page linked from a market_up_down.py HTML
# report ("Click here to read report analysis"). Reads the CSV report for a
# given (or the most recent) run, computes a few highlights directly from the
# numbers, and asks an LLM to write two short sections: one strictly grounded
# in this report's data, and one giving general macro/sector context.
#
# Supports both Gemini and Claude models -- the provider is picked
# automatically from the --model name (a "claude-..." name uses Anthropic,
# anything else is treated as a Gemini model). Requires GEMINI_API_KEY and/or
# ANTHROPIC_API_KEY in .env, matching whichever provider --model selects.
#
# Usage:
#   uv run market_analysis.py --model <model> [-t <timestamp>] [--prompt-file <name>]
#
#   --model         required; a Gemini model (e.g. gemini-2.5-pro) or a
#                   Claude model (e.g. claude-opus-5) -- provider is
#                   detected from the name.
#   -t, --timestamp optional; the YYYYMMDDHHMM timestamp of an existing
#                   market-up-down-*.csv report in OUTPUT_PATH. Defaults to
#                   the most recent report found there.
#   --prompt-file   optional; name of a macro-context prompt file in
#                   config/prompts (file name only, no path). Defaults to
#                   PROMPT_PATH from .env if set, otherwise a built-in
#                   generic macro/sector-rotation prompt.
#
# Output: config/output/market-analysis-YYYYMMDDHHMM.html -- same timestamp
# as the report it analyzes, which is how the main report's analysis link
# finds it.
import os
import re
import sys
import csv
import glob
import html
import argparse
from datetime import datetime
import anthropic
from google import genai
from dotenv import load_dotenv

from market_up_down import REPORT_CSS, render_footer_html, get_absolute_path

load_dotenv()

# Claude's own generation is a short, straightforward writing task (not
# multi-step reasoning), so no thinking/effort configuration is needed here.
ANTHROPIC_MAX_TOKENS = 4096

NUMERIC_FIELDS = {
    "high_price", "low_price", "current_price", "change", "change_median",
    "change_percent", "change_pct_from_high", "change_pct_from_low",
    "change_median_price", "change_pct_from_change_median_price", "change_days",
    "implied_volatility", "expected_move_percent", "realized_vs_implied",
    "realized_volatility", "period_return_percent", "close_in_range_percent",
}

DEFAULT_MACRO_PROMPT = (
    "Comment on the kind of macroeconomic and sector-rotation forces that typically drive the "
    "pattern of volatility seen in the data above -- for example shifts in interest-rate "
    "expectations, dollar strength, rotation between growth and defensive sectors, or capex and "
    "earnings sentiment in heavily-represented sectors. Keep it general and educational rather "
    "than asserting that specific news events occurred."
)

USAGE = (
    "Usage: uv run market_analysis.py --model <model> [-t <timestamp>] [--prompt-file <name>]\n"
    "  --model         required; a Gemini model (e.g. gemini-2.5-pro) or a Claude model\n"
    "                  (e.g. claude-opus-5) -- provider is detected from the name.\n"
    "  -t, --timestamp optional; YYYYMMDDHHMM of an existing market-up-down-*.csv report\n"
    "                  in OUTPUT_PATH. Defaults to the most recent report found.\n"
    "  --prompt-file   optional; name of a macro-context prompt file in config/prompts\n"
    "                  (file name only, no path). Defaults to PROMPT_PATH from .env, or a\n"
    "                  built-in generic prompt.\n"
)

# Styling specific to the analysis page (highlight cards, prose typography); the
# rest of the look -- palette, header, footer -- comes from REPORT_CSS so both
# report pages read as one system.
ANALYSIS_CSS = """
  .back-link { margin-bottom: 16px; font-size: 0.9rem; }
  .back-link a { color: var(--accent); text-decoration: none; }
  .back-link a:hover { text-decoration: underline; }
  .highlights {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
    gap: 12px;
    margin: 20px 0 28px;
  }
  .card {
    background: var(--panel);
    border: 1px solid var(--border);
    border-radius: 10px;
    padding: 14px 16px;
    box-shadow: 0 1px 3px rgba(16, 35, 63, 0.06);
  }
  .card .card-label {
    font-size: 0.72rem;
    text-transform: uppercase;
    letter-spacing: 0.04em;
    color: var(--muted);
    margin: 0 0 4px;
  }
  .card .card-symbol { font-weight: 700; font-size: 1.02rem; margin: 0 0 2px; }
  .card .card-value {
    font-size: 1.35rem;
    font-weight: 700;
    margin: 0 0 2px;
    font-variant-numeric: tabular-nums;
  }
  .card .card-detail { font-size: 0.78rem; color: var(--muted); margin: 0; }
  .card.pos .card-value { color: var(--pos); }
  .card.neg .card-value { color: var(--neg); }
  .prose {
    background: var(--panel);
    border: 1px solid var(--border);
    border-radius: 10px;
    padding: 24px 28px;
    max-width: 820px;
    margin: 0 auto;
  }
  .prose h2 { font-size: 1.25rem; margin: 0 0 12px; }
  .prose h2:not(:first-child) { margin-top: 28px; }
  .prose h3 { font-size: 1.05rem; margin: 20px 0 8px; }
  .prose p { line-height: 1.65; margin: 0 0 12px; }
  .prose ul { margin: 0 0 12px; padding-left: 22px; }
  .prose li { line-height: 1.6; margin-bottom: 6px; }
  .disclaimer {
    text-align: center;
    font-size: 0.78rem;
    color: var(--muted);
    margin: 20px auto 0;
    max-width: 700px;
  }
"""


def usage_error(message):
    print(f"Error: {message}\n", file=sys.stderr)
    print(USAGE, file=sys.stderr)
    sys.exit(2)


def find_latest_timestamp(output_dir):
    """Return the timestamp of the most recent market-up-down-*.csv report, or None."""
    candidates = []
    for path in glob.glob(os.path.join(output_dir, "market-up-down-*.csv")):
        m = re.match(r"market-up-down-(\d{12})\.csv$", os.path.basename(path))
        if m:
            candidates.append(m.group(1))
    return max(candidates) if candidates else None


def read_report_rows(csv_path):
    """Read a market_up_down.py CSV report, coercing numeric fields to float/None."""
    with open(csv_path, newline="") as f:
        rows = []
        for raw in csv.DictReader(f):
            row = dict(raw)
            for key in NUMERIC_FIELDS:
                value = row.get(key, "")
                row[key] = float(value) if value not in (None, "") else None
            rows.append(row)
        return rows


def fmt_pct(value, signed=True):
    if value is None:
        return "n/a"
    return f"{value:+.2f}%" if signed else f"{value:.2f}%"


def fmt_price(value):
    return "n/a" if value is None else f"${value:.2f}"


def fmt_multiple(value):
    return "n/a" if value is None else f"{value:.2f}x"


def compute_highlights(rows):
    """Pick a handful of notable rows directly from the numbers (no AI involved),
    so the headline stats on the page are guaranteed accurate."""
    def best(key, pick_max=True, where=None):
        candidates = [r for r in rows if r.get(key) is not None and (where is None or where(r))]
        if not candidates:
            return None
        return max(candidates, key=lambda r: r[key]) if pick_max else min(candidates, key=lambda r: r[key])

    highlights = []

    gainer = best("change_percent", True)
    if gainer:
        highlights.append({
            "label": "Top Gainer", "symbol": gainer["symbol"],
            "value": fmt_pct(gainer["change_percent"]),
            "detail": f"{fmt_price(gainer['low_price'])} → {fmt_price(gainer['high_price'])}",
            "sign": gainer["change_percent"],
        })

    decliner = best("change_percent", False)
    if decliner:
        highlights.append({
            "label": "Top Decliner", "symbol": decliner["symbol"],
            "value": fmt_pct(decliner["change_percent"]),
            "detail": f"{fmt_price(decliner['high_price'])} → {fmt_price(decliner['low_price'])}",
            "sign": decliner["change_percent"],
        })

    surprise = best("realized_vs_implied", True, where=lambda r: r.get("implied_volatility") is not None)
    if surprise:
        highlights.append({
            "label": "Biggest Vol Surprise", "symbol": surprise["symbol"],
            "value": fmt_multiple(surprise["realized_vs_implied"]),
            "detail": "realized move vs. implied", "sign": 0,
        })

    near_high = best("close_in_range_percent", True)
    if near_high:
        highlights.append({
            "label": "Closed Nearest High", "symbol": near_high["symbol"],
            "value": fmt_pct(near_high["close_in_range_percent"], signed=False),
            "detail": "of its range", "sign": 0,
        })

    near_low = best("close_in_range_percent", False)
    if near_low:
        highlights.append({
            "label": "Closed Nearest Low", "symbol": near_low["symbol"],
            "value": fmt_pct(near_low["close_in_range_percent"], signed=False),
            "detail": "of its range", "sign": 0,
        })

    return highlights


def build_data_table_text(rows):
    """Render every row as a compact, human-readable line, most bullish first --
    this is the only source of specific numbers the model is allowed to cite."""
    ordered = sorted(rows, key=lambda r: r["change_percent"] if r["change_percent"] is not None else 0, reverse=True)
    lines = []
    for r in ordered:
        lines.append(
            f"- {r['symbol']}: change {fmt_pct(r['change_percent'])} "
            f"(low {fmt_price(r['low_price'])} on {r['low_date_hour']} to "
            f"high {fmt_price(r['high_price'])} on {r['high_date_hour']}), "
            f"current {fmt_price(r['current_price'])}, "
            f"implied volatility {fmt_pct(r['implied_volatility'], signed=False)}, "
            f"realized volatility {fmt_pct(r['realized_volatility'], signed=False)}, "
            f"realized/implied {fmt_multiple(r['realized_vs_implied'])}, "
            f"closed {fmt_pct(r['close_in_range_percent'], signed=False)} of its range, "
            f"period return {fmt_pct(r['period_return_percent'])}"
        )
    return "\n".join(lines)


def build_highlights_text(highlights):
    return "\n".join(f"- {h['label']}: {h['symbol']} at {h['value']} ({h['detail']})" for h in highlights)


def resolve_macro_prompt(prompt_file_arg):
    if prompt_file_arg:
        if os.path.basename(prompt_file_arg) != prompt_file_arg:
            usage_error(f"'{prompt_file_arg}' must be a file name only, without a path")
        path = get_absolute_path(os.path.join("./config/prompts", prompt_file_arg))
        if not os.path.isfile(path):
            usage_error(f"prompt file not found: {path}")
        with open(path) as f:
            return f.read()

    env_path = os.getenv("PROMPT_PATH")
    if env_path:
        resolved = get_absolute_path(env_path)
        if os.path.isfile(resolved):
            with open(resolved) as f:
                return f.read()

    return DEFAULT_MACRO_PROMPT


def build_prompt(data_table_text, highlights_text, macro_prompt_text, start_label, end_label, symbol_count):
    return (
        f"Here is the exact per-symbol volatility data for a report covering {start_label} through "
        f"{end_label} for {symbol_count} symbols (sorted by change % descending):\n{data_table_text}\n\n"
        f"Highlights already computed directly from this data (these numbers are authoritative -- do "
        f"not contradict or recompute them):\n{highlights_text}\n\n"
        f"CRITICAL INSTRUCTIONS:\n"
        f"1. Write exactly two sections using these Markdown headers, in this order:\n"
        f"   ### Notable Movers & Volatility\n"
        f"   ### Broader Market Context\n"
        f"2. In 'Notable Movers & Volatility', analyze ONLY the data given above: the biggest movers, "
        f"where implied volatility over- or under-priced the actual move, and where symbols closed "
        f"within their range. Do not invent news, earnings, or other explanations not derivable from "
        f"these numbers.\n"
        f"3. In 'Broader Market Context', add brief macro/sector context that could plausibly relate to "
        f"the kind of volatility seen in this data, using the guidance below. Use general, hedged "
        f"language (e.g. 'likely reflects', 'consistent with') rather than asserting specific news "
        f"events occurred.\n"
        f"4. Keep each section to roughly 120-200 words, in short paragraphs and, where helpful, a "
        f"few bullet points.\n"
        f"---\n"
        f"MACRO CONTEXT GUIDANCE:\n{macro_prompt_text}\n"
    )


def format_inline(text):
    return re.sub(r"\*\*(.*?)\*\*", r"<strong>\1</strong>", html.escape(text))


def markdown_to_html(text):
    """Convert the model's Markdown-ish response into HTML for the .prose container.
    Handles headers, bold, bullet lists, and paragraphs -- the same shapes
    generate_pdf() in market_update_3.py handles for its PDF output."""
    parts = []
    in_list = False

    def close_list():
        nonlocal in_list
        if in_list:
            parts.append("  </ul>")
            in_list = False

    for raw in text.split("\n"):
        line = raw.strip()
        if not line:
            close_list()
            continue

        header_match = re.match(r"^(#{1,6})\s+(.*)$", line)
        bullet_match = re.match(r"^[-*]\s+(.*)$", line)

        if header_match:
            close_list()
            level = 2 if len(header_match.group(1)) <= 3 else 3
            parts.append(f"  <h{level}>{format_inline(header_match.group(2))}</h{level}>")
        elif bullet_match:
            if not in_list:
                parts.append("  <ul>")
                in_list = True
            parts.append(f"    <li>{format_inline(bullet_match.group(1))}</li>")
        else:
            close_list()
            parts.append(f"  <p>{format_inline(line)}</p>")

    close_list()
    return "\n".join(parts)


def slugify_model(model):
    """Make a model name filesystem-safe for use in an output filename."""
    return re.sub(r"[^A-Za-z0-9._-]+", "-", model).strip("-")


def render_analysis_html(highlights, prose_html, start_label, end_label, symbol_count, main_report_href, generated_at, model):
    cards = []
    for h in highlights:
        css_class = " pos" if h["sign"] > 0 else (" neg" if h["sign"] < 0 else "")
        cards.append(
            f'    <div class="card{css_class}">\n'
            f'      <p class="card-label">{html.escape(h["label"])}</p>\n'
            f'      <p class="card-symbol">{html.escape(h["symbol"])}</p>\n'
            f'      <p class="card-value">{html.escape(h["value"])}</p>\n'
            f'      <p class="card-detail">{html.escape(h["detail"])}</p>\n'
            f'    </div>'
        )
    highlights_html = "\n".join(cards)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Blue Sky Stock Volatility Report &mdash; Analysis</title>
<style>{REPORT_CSS}{ANALYSIS_CSS}</style>
</head>
<body>
<div class="wrap">
  <div class="back-link"><a href="{html.escape(main_report_href)}">&larr; Back to full report</a></div>

  <header class="report-header">
    <h1>Blue Sky Stock Volatility Report</h1>
    <p class="subtitle">Analysis &middot; {html.escape(start_label)} &ndash; {html.escape(end_label)}</p>
    <p class="meta">Generated {html.escape(generated_at)} &middot; {symbol_count} symbols &middot; {html.escape(model)}</p>
  </header>

  <div class="highlights">
{highlights_html}
  </div>

  <div class="prose">
{prose_html}
  </div>

  <p class="disclaimer">AI-generated analysis grounded in the data from this report. Informational only, not investment advice.</p>

  {render_footer_html()}
</div>
</body>
</html>
"""


def detect_provider(model):
    """Pick the LLM provider from the --model name: 'claude-...' is Anthropic,
    everything else is treated as a Gemini model name."""
    if model.lower().startswith("claude"):
        return "anthropic"
    return "gemini"


def build_client(provider):
    if provider == "anthropic":
        api_key = os.getenv("ANTHROPIC_API_KEY")
        if not api_key:
            usage_error("ANTHROPIC_API_KEY is not set (add it to .env to use a Claude model)")
        return anthropic.Anthropic(api_key=api_key)

    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        usage_error("GEMINI_API_KEY is not set (add it to .env to use a Gemini model)")
    return genai.Client(api_key=api_key)


def validate_model(client, provider, model):
    print(f"[{datetime.now()}] Validating requested model '{model}'...")
    try:
        if provider == "anthropic":
            available_models = [m.id for m in client.models.list()]
            account_desc = "your Anthropic account"
        else:
            available_models = [m.name.replace("models/", "") for m in client.models.list()]
            account_desc = "your Google AI Studio account"
        if model not in available_models:
            print(f"Error: Model '{model}' is not currently available or active in {account_desc}.")
            sys.exit(1)
    except SystemExit:
        raise
    except Exception as e:
        print(f"Warning: Could not validate model list automatically: {e}")
        print("Proceeding with requested model anyway...")


def generate_narrative(client, provider, model, prompt_text):
    if provider == "anthropic":
        response = client.messages.create(
            model=model,
            max_tokens=ANTHROPIC_MAX_TOKENS,
            messages=[{"role": "user", "content": prompt_text}],
        )
        return "".join(block.text for block in response.content if block.type == "text")

    response = client.models.generate_content(model=model, contents=prompt_text)
    return response.text


def main():
    parser = argparse.ArgumentParser(description="Generate an AI-written analysis page for a market_up_down.py report.")
    parser.add_argument("--model", type=str, required=True,
                         help="Model to use: a Gemini model (e.g. gemini-2.5-pro) or a Claude model "
                              "(e.g. claude-opus-5) -- provider is detected from the name")
    parser.add_argument("-t", "--timestamp", type=str, default=None,
                         help="YYYYMMDDHHMM of an existing market-up-down report; defaults to the most recent one")
    parser.add_argument("--prompt-file", type=str, default=None,
                         help="Name of a macro-context prompt file in config/prompts (file name only)")
    args = parser.parse_args()

    output_dir = os.getenv("OUTPUT_PATH")
    timestamp = args.timestamp or find_latest_timestamp(output_dir)
    if timestamp is None:
        usage_error(f"no market-up-down-*.csv reports found in {output_dir}")

    csv_path = os.path.join(output_dir, f"market-up-down-{timestamp}.csv")
    if not os.path.isfile(csv_path):
        usage_error(f"report not found: {csv_path}")

    main_report_html = f"market-up-down-{timestamp}.html"
    model_slug = slugify_model(args.model)
    analysis_html_path = os.path.join(output_dir, f"market-analysis-{timestamp}-{model_slug}.html")
    # The main report's "read report analysis" link always points at this
    # unsuffixed name (it's generated before any model is chosen); keep it
    # working by refreshing it with whichever model's analysis ran last.
    latest_analysis_html_path = os.path.join(output_dir, f"market-analysis-{timestamp}.html")

    rows = read_report_rows(csv_path)
    if not rows:
        usage_error(f"'{csv_path}' has no data rows")

    start_label = rows[0]["start_date"]
    end_label = rows[0]["end_date"]

    highlights = compute_highlights(rows)
    data_table_text = build_data_table_text(rows)
    highlights_text = build_highlights_text(highlights)
    macro_prompt_text = resolve_macro_prompt(args.prompt_file)
    prompt = build_prompt(data_table_text, highlights_text, macro_prompt_text, start_label, end_label, len(rows))

    provider = detect_provider(args.model)
    client = build_client(provider)
    validate_model(client, provider, args.model)

    print(f"[{datetime.now()}] Analyzing {len(rows)} symbols from {os.path.basename(csv_path)} with {args.model} ({provider})...")
    try:
        narrative = generate_narrative(client, provider, args.model, prompt)
    except Exception as e:
        print(f"Error: {provider} request failed: {e}", file=sys.stderr)
        sys.exit(1)

    prose_html = markdown_to_html(narrative)

    html_report = render_analysis_html(
        highlights,
        prose_html,
        start_label,
        end_label,
        symbol_count=len(rows),
        main_report_href=main_report_html,
        generated_at=datetime.now().strftime("%B %d, %Y %I:%M %p"),
        model=args.model,
    )
    with open(analysis_html_path, "w") as f:
        f.write(html_report)
    with open(latest_analysis_html_path, "w") as f:
        f.write(html_report)

    print(f"[{datetime.now()}] Wrote output to {analysis_html_path}")
    print(f"[{datetime.now()}] Wrote output to {latest_analysis_html_path} (updated to reflect this run)")


if __name__ == "__main__":
    main()
