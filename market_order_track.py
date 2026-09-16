# market_order_track.py
# revision history
# revised on 09/15/26 - Copied from market_update_3.py, removed all code except imports and load_dotenv()
# revised on 09/15/26 - Added main() function and if __name__ == "__main__" block
# revised on 09/15/26 - Added requirements below:
# 1. argparse for command line arguments - file and date
# 2. If file is not provided, use default file name "market_order_track.csv" in INPUT_PATH directory defined in .env
# 3. If date is not provided, use today's date in YYYY-MM-DD format
# 4. Populate Sector column with GICS sector description using yfinance library for each stock in the file
# 5. Populate Report Price column with current stock price using yfinance library for each stock in the file; if Date is provided, populate Report Price column with stock price on that date using yfinance library for each stock in the file.
# 6. Generate a PDF report with the following information:
#    a. Title: "Market Order Track Report"
#    b. Date: Today's date in YYYY-MM-DD format
#    c. Table with the following columns: Ticker, Sector, Name of Security, Status, Action, Quantity|Face Value, Price, Timing, Fill Price, Trade Time and Date (ET), Report Price, Report Price Date (ET), Gain|Loss, Gain|Loss %
#    d. Use this agrithm to calculate Gain|Loss and Gain|Loss %:
#       Gain|Loss = (Current Price - Fill Price) * Quantity|Face Value
# 7. Save the PDF report with the name "market_order_track_report_yyyymmddhhmm.pdf" in OUTPUT_PATH directory defined in .env
# revised on 09/15/26 - Implemented requirements 1-7; also writes the populated CSV next to the PDF
#
import os
import re
import sys
import csv
import argparse
import yfinance as yf
from datetime import datetime, date, timedelta
from zoneinfo import ZoneInfo
from xml.sax.saxutils import escape
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter, landscape
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
from dotenv import load_dotenv

load_dotenv()

ET = ZoneInfo("America/New_York")
DEFAULT_INPUT_FILE = "market_order_track.csv"

# Columns of the PDF table, in order (requirement 6c)
REPORT_COLUMNS = [
    "Ticker", "Sector", "Name of Security", "Status", "Action", "Quantity|Face Value",
    "Price", "Timing", "Fill Price", "Trade Time and Date (ET)", "Report Price",
    "Report Price Date (ET)", "Gain|Loss", "Gain|Loss %",
]

# Brokerage export headers that differ from the report column names
HEADER_ALIASES = {
    "Symbol": "Ticker",
    "Time and Date(ET)": "Trade Time and Date (ET)",
    "Report Time and Date (ET)": "Report Price Date (ET)",
    "Gain|Losss": "Gain|Loss",
}


def resolve_input_file(file_arg):
    """Returns the CSV path to read: --file if given, otherwise the default file in INPUT_PATH."""
    if file_arg:
        path = file_arg
    else:
        input_dir = os.getenv("INPUT_PATH", ".")
        path = os.path.join(input_dir, DEFAULT_INPUT_FILE)

    if not os.path.isfile(path):
        print(f"Error: Input file '{path}' not found.")
        sys.exit(1)
    return path


def read_orders(path):
    """Reads the order CSV into a list of dicts keyed by the report column names."""
    orders = []
    with open(path, "r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            normalized = {}
            for key, value in row.items():
                if key is None:
                    continue
                key = HEADER_ALIASES.get(key.strip(), key.strip())
                normalized[key] = (value or "").strip()
            if normalized.get("Ticker"):
                orders.append(normalized)
    return orders


def parse_quantity(text):
    """'1,000 Shares' -> 1000.0 ; '2.0074 Shares' -> 2.0074 ; returns None if not parseable."""
    match = re.search(r"[-+]?[\d,]*\.?\d+", text or "")
    if not match:
        return None
    try:
        return float(match.group(0).replace(",", ""))
    except ValueError:
        return None


def parse_money(text):
    """'$12.39 ' -> 12.39 ; '-' -> None."""
    match = re.search(r"[-+]?[\d,]*\.?\d+", text or "")
    if not match:
        return None
    try:
        return float(match.group(0).replace(",", ""))
    except ValueError:
        return None


def get_sector(ticker_obj):
    """GICS sector for equities; fund category for ETFs; quote type as a last resort."""
    try:
        info = ticker_obj.info or {}
    except Exception as e:
        print(f"   Warning: could not fetch info for {ticker_obj.ticker}: {e}")
        return "N/A"
    return info.get("sector") or info.get("category") or info.get("quoteType") or "N/A"


def get_report_price(ticker_obj, as_of):
    """
    Returns (price, price_date_str).
    as_of=None  -> current price and the current ET timestamp.
    as_of=date  -> last close on or before that date and the date of that close.
    """
    try:
        if as_of is None:
            price = ticker_obj.fast_info.get("lastPrice")
            if price is None:
                hist = ticker_obj.history(period="5d")
                if hist.empty:
                    return None, ""
                price = float(hist["Close"].iloc[-1])
            return float(price), datetime.now(ET).strftime("%Y-%m-%d %H:%M")

        # Look back a week so weekends/holidays resolve to the prior trading day
        hist = ticker_obj.history(start=as_of - timedelta(days=7), end=as_of + timedelta(days=1))
        if hist.empty:
            return None, ""
        last = hist.iloc[-1]
        return float(last["Close"]), hist.index[-1].strftime("%Y-%m-%d")
    except Exception as e:
        print(f"   Warning: could not fetch price for {ticker_obj.ticker}: {e}")
        return None, ""


def populate_orders(orders, as_of):
    """Fills Sector, Report Price, Report Price Date (ET), Gain|Loss and Gain|Loss % in place."""
    cache = {}
    for order in orders:
        ticker = order["Ticker"]
        if ticker not in cache:
            print(f"[{datetime.now()}] Fetching {ticker}...")
            ticker_obj = yf.Ticker(ticker)
            cache[ticker] = (get_sector(ticker_obj), *get_report_price(ticker_obj, as_of))

        sector, price, price_date = cache[ticker]
        order["Sector"] = sector
        order["Report Price"] = f"{price:.2f}" if price is not None else ""
        order["Report Price Date (ET)"] = price_date

        # Buy:  Gain|Loss = (Current Price - Fill Price) * Quantity|Face Value
        # Sell: sign is flipped, so a price rise after selling shows as a loss
        fill = parse_money(order.get("Fill Price"))
        qty = parse_quantity(order.get("Quantity|Face Value"))
        if price is not None and fill and qty is not None:
            direction = -1 if order.get("Action", "").strip().lower() == "sell" else 1
            gain = direction * (price - fill) * qty
            order["Gain|Loss"] = f"{gain:.2f}"
            order["Gain|Loss %"] = f"{direction * (price - fill) / fill * 100:.2f}"
        else:
            order["Gain|Loss"] = ""
            order["Gain|Loss %"] = ""
    return orders


def write_csv(orders, csv_path):
    """Writes the populated orders back out with the report column layout."""
    with open(csv_path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=REPORT_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(orders)


def generate_pdf(orders, pdf_path, as_of):
    doc = SimpleDocTemplate(
        pdf_path, pagesize=landscape(letter), leftMargin=24, rightMargin=24, topMargin=36, bottomMargin=36
    )

    styles = getSampleStyleSheet()
    title_style = styles["Heading1"]
    title_style.fontSize = 16
    title_style.leading = 20

    body_style = styles["Normal"]
    body_style.fontSize = 10
    body_style.leading = 14

    cell_style = ParagraphStyle("cell", parent=styles["Normal"], fontSize=6.5, leading=8)
    head_style = ParagraphStyle("head", parent=cell_style, fontName="Helvetica-Bold")

    story = []
    story.append(Paragraph("Market Order Track Report", title_style))
    story.append(Paragraph(f"Date: {date.today().strftime('%Y-%m-%d')}", body_style))
    price_basis = as_of.strftime("%Y-%m-%d") if as_of else "current market price"
    story.append(Paragraph(f"Report Price basis: {price_basis}", body_style))
    story.append(Spacer(1, 12))

    # Header row + one row per order; wrap every cell in a Paragraph so long names break cleanly.
    # Cell text is XML-escaped because Paragraph treats '&' and '<' as markup (e.g. "AT&T INC").
    data = [[Paragraph(escape(col), head_style) for col in REPORT_COLUMNS]]
    for order in orders:
        gain = parse_money(order.get("Gain|Loss"))
        gain_color = "" if gain is None else ("#1B7F3B" if gain >= 0 else "#B00020")
        row = []
        for col in REPORT_COLUMNS:
            text = escape(re.sub(r"\s+", " ", order.get(col, "")))
            # Colour gains green and losses red
            if gain_color and col in ("Gain|Loss", "Gain|Loss %"):
                text = f'<font color="{gain_color}">{text}</font>'
            row.append(Paragraph(text, cell_style))
        data.append(row)

    col_widths = [40, 62, 96, 40, 38, 60, 60, 42, 40, 62, 40, 62, 44, 44]
    table = Table(data, colWidths=col_widths, repeatRows=1)
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#D9E1F2")),
        ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F5F5F5")]),
    ]))

    story.append(table)
    doc.build(story)


def main():
    # --- ARGUMENT PARSING & VALIDATION ---
    parser = argparse.ArgumentParser(description="Generate a Market Order Track PDF report from a brokerage order CSV.")
    parser.add_argument("--file", type=str, default=None,
                        help=f"Order CSV to read (default: {DEFAULT_INPUT_FILE} in INPUT_PATH)")
    parser.add_argument("--date", type=str, default=None,
                        help="Report Price date in YYYY-MM-DD (default: today, using current market price)")
    args = parser.parse_args()

    as_of = None
    if args.date:
        try:
            as_of = datetime.strptime(args.date, "%Y-%m-%d").date()
        except ValueError:
            print(f"Error: Invalid date '{args.date}'. Must be in YYYY-MM-DD format.")
            sys.exit(1)
        if as_of > date.today():
            print(f"Error: Date '{args.date}' is in the future.")
            sys.exit(1)
        # Today's date means the current price, same as no date at all
        if as_of == date.today():
            as_of = None

    input_path = resolve_input_file(args.file)
    output_dir = os.getenv("OUTPUT_PATH", ".")
    os.makedirs(output_dir, exist_ok=True)

    print(f"[{datetime.now()}] Input: {input_path} | Report Price date: {args.date or date.today().isoformat()}")

    # --- EXECUTION ---
    orders = read_orders(input_path)
    if not orders:
        print("Error: No orders found in the input file.")
        sys.exit(1)

    populate_orders(orders, as_of)

    timestamp_str = datetime.now().strftime("%Y%m%d%H%M")
    pdf_path = os.path.join(output_dir, f"market_order_track_report_{timestamp_str}.pdf")
    csv_path = os.path.join(output_dir, f"market_order_track_{timestamp_str}.csv")

    write_csv(orders, csv_path)
    generate_pdf(orders, pdf_path, as_of)

    print(f"[{datetime.now()}] Report generated: {pdf_path}")
    print(f"[{datetime.now()}] Populated CSV saved: {csv_path}")


if __name__ == "__main__":
    main()
