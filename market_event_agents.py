# market_event_agents.py
# Revision history
# Copied from market_agent.py on 09/21/26 as the starting point for a new agent that will generate market event summaries.
# Requirements, 09/21/26:
# 1. This script is an agentic application that gathers information about events and gauge how those events may affect stock market performance.
# 2. Multiple agents watch out for events in multiple areas. The script starts with two agents. Agent 1 for macroeconomics, Agent 2 for geopolitics. The script should be able to employ more agents for more areas down the road. 
# 3. Agent 1 gathers information about macroeconomic events, such as inflation, interest rate changes, US trade data, employment data from governemnet agencies and other relevent sources.
# 4. Agent 2 gathers information about geopolitical events, such as international conflicts, policy changes, and other relevant developments from major news outlets.
# 5. Each agent use selected model API to generate a summary of area events and their potential impact on the stock market. Each agent accumulates its findings and augment the prompt with its findings so as to gain a more comprehensive understanding and deepen its expertise.
# 6. The script will compile summaries from all agents to create a comprehensive market event report.
# Note: Please evaluate an open source project "edict" cloned at ~/agents/edict and see if its design and setup can be used for this app. 
# Project edict is a multi-agent system that uses a modular architecture to gather and analyze information from various sources. It employs multiple agents, each with specific roles and responsibilities, to collect data, process it, and generate insights. The agents communicate with each other and share information to create a comprehensive understanding of the subject matter. The design of edict can be adapted for this market event agent application by implementing similar modularity, communication protocols, and data processing techniques to effectively gather and analyze macroeconomic and geopolitical events impacting the stock market.
#
# Purpose in the wider report set, 09/22/2026:
# This script exists to improve the Direction / Signal (Buy / Sell / Hold) tag in
# market_up_down_concise.py with evidence from OUTSIDE the ^VIX regime. Today that
# tag is built from price action alone -- close-in-range, period return, expected
# move -- with ^VIX as the only external input, and ^VIX only ever vetoes a Buy.
# It cannot see a CPI print, an FOMC decision, a tariff announcement or a shooting
# war. The event report produced here is meant to become that missing input: a
# per-area read (macro, geopolitics, ...) that can confirm, temper or override a
# price-derived tag. Design decisions below should be judged on whether they make
# that hand-off possible, not only on whether the report reads well on its own.
# How the hand-off actually works (a shared file the concise report reads, a
# regime field it consumes, or a manual read by the user) is itself an open
# question -- see pending decision 6.
#
# Evaluation of edict (answering the note above), 09/22/2026:
# edict is not a library that can be imported; it is an OpenClaw orchestration
# layout. Its "agents" are OpenClaw sessions defined by persona files
# (agents/<dept>/SOUL.md) and its Python is glue -- a kanban CLI, a Feishu
# bridge, an RSS fetcher, a stdlib dashboard server. The newer architecture doc
# (edict_agent_architecture.md) adds Postgres, Redis, FastAPI and React. Its value
# is a plan/review gate, a live kanban and human intervention on long open-ended
# tasks; this script's pipeline is fixed (N agents research in parallel, then one
# compile step) and needs none of that. So: do not adopt edict as a framework.
# Three ideas from it are worth borrowing, and the sketch below assumes them:
#   - one prompt file per agent (the SOUL.md pattern), so adding an area is
#     adding a file, not editing code (requirement 2)
#   - a per-agent memory/archive file (edict's "memorials"), which is how
#     requirement 5 (accumulated findings augmenting the prompt) is met
#   - the RSS source lists in edict's scripts/fetch_morning_news.py as a template
#     for source selection (its Reuters URLs are dead -- template, not drop-in)
#
# Proposed design sketch (not yet implemented, pending the decisions below):
#   uv run market_event_agents.py --model <model> [-a macro,geopolitics] [-d 1]
#   config/prompts/agents/<area>.md   persona, sources, what "market impact" means
#   config/memory/<area>.md           rolling dated findings, appended each run
#   config/output/market-events-YYYYMMDDHHMM.(html|md)
#   - an AGENTS registry maps area name -> (prompt file, memory file, sources);
#     -a selects which areas run; agents run concurrently since they are
#     independent
#   - each agent gathers with the model's built-in web search / fetch, steered at
#     its own sources by its prompt (BLS/BEA/Fed/Census/Treasury for macro;
#     Reuters/AP/BBC/FT for geopolitics), and returns a summary with source URLs
#   - after each run the agent's summary is appended to its memory file with the
#     date; the next run gets the most recent entries back in its prompt, and the
#     agent condenses its own memory every so often so it stops growing
#   - a final compile call turns all agent summaries into the report: overall
#     regime, per-area sections, sector implications, a watch list
#   - provider detection follows market_analysis.py (a "claude-..." model name
#     uses Anthropic, anything else Gemini)
#
# Pending decisions, 09/22/2026 -- awaiting the user; do not implement until
# these are settled:
# 1. Gathering method: (a) model built-in web search + fetch (least code, sources
#    visible, recommended for v1), (b) Python RSS fetch + model summary (cheaper,
#    auditable, headlines only), or (c) both.
# 2. Provider: make Claude the primary, tested path and drop this file's inherited
#    Gemini body? Gemini would need its own grounding tool rather than Claude's
#    web search, so supporting both costs a second code path.
# 3. Output format: HTML (like the up/down reports), PDF (like market_agent.py),
#    or both -- and is a Chinese version wanted, as market_agent.py produces?
# 4. Agent roster at v1: keep politics (elections, fiscal and tax policy) folded
#    into macro and geopolitics, or stand it up as agent 3 from the start? The
#    earlier plan for this script listed rates, jobs, trade wars, geopolitics and
#    politics as the areas to cover.
# 5. Memory horizon: feed back the last ~10 runs with periodic self-condensation,
#    or feed the full accumulated history every run?
# 6. Hand-off to market_up_down_concise.py: what does this script write that the
#    concise report can consume -- e.g. a small dated event-regime file (risk-on /
#    neutral / risk-off plus per-sector tilts) that get_direction() reads
#    alongside ^VIX, or is the report read by the user only for now? This decides
#    whether the concise script changes at all, and which fields it needs.
#
# The following are the comments from the original market_agent.py script for reference only that may not be all directly revelant for this script
# Comments from the original market_agent.py script:
# Read tickers from a file defined in .env
# revision history
# revised on 06/24/26
# 1. copied from market_recap.py
# 2. Added read_tickers() to read tickers from a file defined in .env
# 3. Added get_market_data() to fetch live stock data using yfinance
# 4. Added read_prompt() to get prompt from a file defined in .env
# 5. Added augment_prompt() to inject live data into the prompt
# 6. Added main() to run
# 7. Added Chinese fonts
# 8. Added deep_translator to translate recap text to Chinese, it is not used for now 
# 9. prompt file creation process - 
#    Created manually in Obsidian, saved to Obsidian Vault, copied to config/propmts. 
import os
import re
import yfinance as yf
from datetime import datetime
from google import genai
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from dotenv import load_dotenv
# from deep_translator import GoogleTranslator - for future use

load_dotenv()

# Helper: Get absolute path relative to the script location
def get_absolute_path(path):
    if path.startswith('.'):
        script_dir = os.path.dirname(os.path.abspath(__file__))
        return os.path.join(script_dir, path.lstrip('./'))
    return path

def read_tickers(tickers_path):
    # "#" lines are comments, e.g. the "# merged from ..." trailer that
    # market_stock_sector.py -m writes.
    with open(get_absolute_path(tickers_path), "r") as f:
        return [line.strip() for line in f.readlines()
                if line.strip() and not line.strip().startswith("#")]

def get_market_data(tickers):
    data_points = {}
    for ticker in tickers:
        stock = yf.Ticker(ticker)
        hist = stock.history(period="1d")
        if not hist.empty:
            close = hist['Close'].iloc[-1]
            prev = hist['Open'].iloc[0]
            change = ((close - prev) / prev) * 100
            data_points[ticker] = f"{close:.2f} ({change:+.2f}%)"
    return data_points

def read_prompt(prompt_path):
    with open(get_absolute_path(prompt_path), 'r') as f:
        return f.read()

def augment_prompt(base_prompt, data_points):
    data_summary = "\n".join([f"- {t}: {v}" for t, v in data_points.items()])
    return f"Use this live data as evidence:\n{data_summary}\n---\n{base_prompt}"

def generate_pdf(text, filename, use_chinese=False):
        
     # Configure PDF Output Destination
    today_str = datetime.now().strftime("%Y-%m-%d")
    script_dir = os.path.dirname(os.path.abspath(__file__))
    if use_chinese:
        pdf_filename = os.path.join(script_dir, f"market_recap_{today_str}_zh.pdf")   
    else:
        pdf_filename = os.path.join(script_dir, f"market_recap_{today_str}.pdf")   
    
    # Set up document geometry
    doc = SimpleDocTemplate(
        pdf_filename,
        pagesize=letter,
        leftMargin=54,
        rightMargin=54,
        topMargin=54,
        bottomMargin=54,
    )
          
    styles = getSampleStyleSheet()
    story = []
    
    # Font Registration
    if use_chinese:
        font_path = '/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc'
        pdfmetrics.registerFont(TTFont('ChineseFont', font_path))
        font_name = 'ChineseFont'
    else:
        font_name = 'Helvetica'

    title_style = styles["Heading1"]
    title_style.fontSize = 16
    title_style.leading = 20

    body_style = styles["Normal"]
    body_style.fontName = font_name
    body_style.fontSize = 10
    body_style.leading = 15

    # Add Header
    story.append(Paragraph("Daily Stock Market Recap", title_style))
    story.append(
        Paragraph(
            f"Generated on: {datetime.now().strftime('%B %d, %Y at %I:%M %p')}",
            body_style,
        )
    )
    story.append(Spacer(1, 15))

    # Clean and parse Markdown formatting into ReportLab XML syntax
    for line in text.split("\n"):
        line = line.strip()
        if not line:
            continue

        # Convert Markdown bold (**text**) to bold tags (<b>text</b>)
        formatted_line = re.sub(r"\*\*(.*?)\*\*", r"<b>\1</b>", line)

        # Format bullet points cleanly
        if formatted_line.startswith("* ") or formatted_line.startswith("- "):
            formatted_line = f"•  {formatted_line[2:]}"

        # Format subheadings
        if formatted_line.startswith("#"):
            header_text = formatted_line.lstrip("# ").strip()
            formatted_line = f"<font size=12><b>{header_text}</b></font>"

        story.append(Paragraph(formatted_line, body_style))
        story.append(Spacer(1, 6))    

    doc.build(story)

def main():
    client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))
    
    # 1. Fetch Data & Prepare Prompt
    tickers = read_tickers(os.getenv("TICKERS_PATH"))
    data = get_market_data(tickers)
    base_prompt = read_prompt(os.getenv("PROMPT_PATH"))
    final_prompt = augment_prompt(base_prompt, data)
    
    # 2. Generate English Recap
    print(f"[{datetime.now()}] Generating English report...")
    response = client.models.generate_content(model="gemini-2.5-flash", contents=final_prompt)
    english_text = response.text
    
    today_str = datetime.now().strftime("%Y-%m-%d")
    generate_pdf(english_text, f"market_recap_{today_str}.pdf", use_chinese=False)
    
    # 3. Generate Chinese Recap
    print(f"[{datetime.now()}] Translating to Chinese...")
    trans_prompt = f"Translate to professional, concise financial Chinese:\n{english_text}"
    zh_response = client.models.generate_content(model="gemini-2.5-flash", contents=trans_prompt)
    generate_pdf(zh_response.text, f"market_recap_{today_str}_zh.pdf", use_chinese=True)

    print(f"[{datetime.now()}] Both reports generated successfully.")

if __name__ == "__main__":
    main()
