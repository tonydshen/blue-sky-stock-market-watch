# market_update_3.py
# revision history
# revised on 06/24/26 - Base setup and translation
# revised on 06/25/26 - PDF pathing and fonts
# revised on 07/07/26 - Switched to argparse for strict model and period validation
# revised on 07/07/26 - Updated filename timestamp to include hour and minute (YYYYMMDDHHMM)
# revised on 07/08/26 - Added lightweight RAG file ingestion (--input yes) for TXT, CSV, PDF, and Images

import os
import re
import sys
import argparse
import yfinance as yf
from datetime import datetime
from google import genai
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from dotenv import load_dotenv

load_dotenv()

def get_absolute_path(path):
    if path.startswith('.'):
        script_dir = os.path.dirname(os.path.abspath(__file__))
        return os.path.join(script_dir, path.lstrip('./'))
    return path

def read_tickers(tickers_path):
    with open(get_absolute_path(tickers_path), "r") as f:
        return [line.strip() for line in f.readlines() if line.strip()]

def read_prompt(prompt_path):
    with open(get_absolute_path(prompt_path), 'r') as f:
        return f.read()

def get_market_data(tickers, period):
    """Fetches historical data strictly using the validated yfinance period."""
    data_points = {}
    
    for ticker in tickers:
        stock = yf.Ticker(ticker)
        hist = stock.history(period=period)
        
        if not hist.empty and len(hist) >= 2:
            current_close = hist['Close'].iloc[-1]
            past_close = hist['Close'].iloc[0]
            change = ((current_close - past_close) / past_close) * 100
            data_points[ticker] = f"{current_close:.2f} ({change:+.2f}% over {period})"
        elif not hist.empty:
            current_close = hist['Close'].iloc[-1]
            data_points[ticker] = f"{current_close:.2f} (Insufficient data to calculate {period} change)"
        else:
            data_points[ticker] = "No data available"
                
    return data_points

def process_input_files(input_dir, client):
    """Reads local text/csv files and uploads media/PDF files to Gemini API."""
    extra_texts = []
    media_files = []
    
    if not input_dir or not os.path.exists(input_dir):
        print(f"Warning: Input directory '{input_dir}' does not exist or is not defined.")
        return extra_texts, media_files

    supported_media = ['.pdf', '.jpg', '.jpeg', '.png']
    supported_text = ['.txt', '.csv']

    for filename in os.listdir(input_dir):
        filepath = os.path.join(input_dir, filename)
        if not os.path.isfile(filepath): 
            continue
            
        ext = os.path.splitext(filename)[1].lower()
        
        # 1. Process Text and CSVs locally
        if ext in supported_text:
            try:
                with open(filepath, 'r', encoding='utf-8') as f:
                    extra_texts.append(f"\n--- CONTENT OF {filename} ---\n{f.read()}\n--- END OF {filename} ---\n")
                print(f"[{datetime.now()}] Read text file: {filename}")
            except Exception as e:
                print(f"Error reading {filename}: {e}")
                
        # 2. Upload PDFs and Images via Gemini File API
        elif ext in supported_media:
            try:
                print(f"[{datetime.now()}] Uploading multimodal file to Gemini: {filename}...")
                uploaded_file = client.files.upload(file=filepath)
                media_files.append(uploaded_file)
            except Exception as e:
                print(f"Error uploading {filename}: {e}")

    return extra_texts, media_files

def augment_prompt(base_prompt, data_points, period, extra_texts):
    """Injects data and supplementary text with strict anti-hallucination and relevance guardrails."""
    data_summary = "\n".join([f"- {t}: {v}" for t, v in data_points.items()])
    
    guardrails = (
        f"Here is the exact market data reflecting a {period} timeframe:\n{data_summary}\n\n"
        f"CRITICAL INSTRUCTIONS:\n"
        f"1. You are analyzing a {period} performance window. Ensure your narrative reflects this specific timeframe.\n"
        f"2. ONLY use the data provided above and any supplementary files/text provided. Do not invent, guess, or hallucinate external news events or earnings reports.\n"
        f"3. DOCUMENT RELEVANCE FILTER: I have attached supplementary files (PDFs, images, CSVs, text). You must evaluate their relevance to the main topic and scope outlined in my base prompt below. If the content of an attached file or chart is irrelevant to the prompt's focus, you MUST IGNORE IT completely.\n"
        f"4. If the specific reason for a price movement is not provided in my prompt or the relevant supplementary files, simply state the performance numbers without speculating why.\n"
        f"---\n"
    )
    
    combined_extra_text = "".join(extra_texts)
    if combined_extra_text:
        combined_extra_text = f"\n\nSUPPLEMENTARY TEXT/CSV DATA:\n{combined_extra_text}\n"
        
    return guardrails + combined_extra_text + base_prompt

def generate_pdf(text, filename, use_chinese=False):
    output_dir = os.getenv("OUTPUT_PATH", ".")
    pdf_filename = os.path.join(output_dir, filename)
    
    if use_chinese:
        font_path = os.getenv("CHINESE_FONT_PATH")
        pdfmetrics.registerFont(TTFont('ChineseFont', font_path))
        font_name = 'ChineseFont'
    else:
        font_name = 'Helvetica'

    doc = SimpleDocTemplate(
        pdf_filename, pagesize=letter, leftMargin=54, rightMargin=54, topMargin=54, bottomMargin=54
    )

    styles = getSampleStyleSheet()
    story = []

    title_style = styles["Heading1"]
    title_style.fontSize = 16
    title_style.leading = 20

    body_style = styles["Normal"]
    body_style.fontName = font_name
    body_style.fontSize = 10
    body_style.leading = 15

    story.append(Paragraph("Market Recap Report", title_style))
    story.append(Paragraph(f"Generated on: {datetime.now().strftime('%B %d, %Y at %I:%M %p')}", body_style))
    story.append(Spacer(1, 15))

    for line in text.split("\n"):
        line = line.strip()
        if not line:
            continue

        formatted_line = re.sub(r"\*\*(.*?)\*\*", r"<b>\1</b>", line)

        if formatted_line.startswith("* ") or formatted_line.startswith("- "):
            formatted_line = f"•  {formatted_line[2:]}"

        if formatted_line.startswith("#"):
            header_text = formatted_line.lstrip("# ").strip()
            formatted_line = f"<font size=12><b>{header_text}</b></font>"

        story.append(Paragraph(formatted_line, body_style))
        story.append(Spacer(1, 6))

    doc.build(story)

def main():
    # --- ARGUMENT PARSING & VALIDATION ---
    parser = argparse.ArgumentParser(description="Generate AI-powered market recap reports.")
    parser.add_argument("--model", type=str, required=True, help="Gemini model to use (e.g., gemini-2.5-pro)")
    parser.add_argument("--period", type=str, required=True, help="Historical data period (e.g., 1d, 5d, 1mo, 3mo, 6mo, ytd, 1y)")
    parser.add_argument("--input", type=str, choices=['yes', 'no'], default='no', help="Include supplementary files from INPUT_PATH (yes/no)")
    args = parser.parse_args()

    # 1. Validate Period Argument
    period_input = args.period.lower()
    
    if period_input == '1m': period_input = '1mo'
    elif period_input == '3m': period_input = '3mo'
    elif period_input == '6m': period_input = '6mo'
    
    valid_periods = ['1d', '5d', '1mo', '3mo', '6mo', 'ytd', '1y', '2y', '5y', '10y', 'max']
    if period_input not in valid_periods:
        print(f"Error: Invalid period '{args.period}'. Must be one of: {', '.join(valid_periods)}")
        sys.exit(1)

    # 2. Client Setup & Validate Model Argument
    client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))
    
    print(f"[{datetime.now()}] Validating requested model '{args.model}'...")
    try:
        available_models = [m.name.replace('models/', '') for m in client.models.list()]
        
        if args.model not in available_models:
            print(f"Error: Model '{args.model}' is not currently available or active in your Google AI Studio account.")
            sys.exit(1)
    except Exception as e:
        print(f"Warning: Could not validate model list automatically: {e}")
        print("Proceeding with requested model anyway...")

    print(f"[{datetime.now()}] Setup complete. Using Model: {args.model} | Period: {period_input} | Input Files: {args.input}")

    # --- EXECUTION ---
    base_prompt = read_prompt(os.getenv("PROMPT_PATH"))
    tickers = read_tickers(os.getenv("TICKERS_PATH"))
    data = get_market_data(tickers, period_input)
    
    # Process Supplementary Files
    extra_texts = []
    media_files = []
    if args.input == 'yes':
        input_dir = os.getenv("INPUT_PATH")
        extra_texts, media_files = process_input_files(input_dir, client)

    # Augment Prompt
    final_prompt_text = augment_prompt(base_prompt, data, period_input, extra_texts)

    # Construct the multimodal contents payload
    # The GenAI SDK allows passing a list of mixed types (file objects + strings)
    contents_payload = media_files + [final_prompt_text]

    timestamp_str = datetime.now().strftime("%Y%m%d%H%M")

    # Generate English Recap
    print(f"[{datetime.now()}] Generating English report...")
    response = client.models.generate_content(model=args.model, contents=contents_payload)
    english_text = response.text

    generate_pdf(english_text, f"market_recap_{timestamp_str}.pdf", use_chinese=False)

    # Generate Chinese Recap
    print(f"[{datetime.now()}] Translating to Chinese...")
    trans_prompt = (
        f"Translate the following market recap into professional, concise financial Chinese. "
        f"Maintain the exact formatting, numbers, and bullet points. Do not add external commentary:\n\n{english_text}"
    )
    zh_response = client.models.generate_content(model=args.model, contents=trans_prompt)
    generate_pdf(zh_response.text, f"market_recap_{timestamp_str}_zh.pdf", use_chinese=True)

    print(f"[{datetime.now()}] Both reports generated successfully: market_recap_{timestamp_str}.pdf")

    # --- CLEANUP ---
    # Delete uploaded media files from the Gemini API to prevent storage bloat
    if media_files:
        print(f"[{datetime.now()}] Cleaning up uploaded files from API storage...")
        for uploaded_file in media_files:
            try:
                client.files.delete(name=uploaded_file.name)
            except Exception as e:
                print(f"Could not delete {uploaded_file.name}: {e}")

if __name__ == "__main__":
    main()