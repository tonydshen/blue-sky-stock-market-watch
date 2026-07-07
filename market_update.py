# market_update.py
# backup copy - market_agent.py
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
# revised on 06/25/26
# 1. OUTPUT_PATH defined in .env for PDF report outputs
# 2. INPUT_PATH defined in .env for input files to be augmented into the prompt
# 3. ARCHIVE_PATH defined in .env for archiving reports
# 4. CHINESE_FONT_PATH defined in .env 
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
    with open(get_absolute_path(tickers_path), "r") as f:
        return [line.strip() for line in f.readlines() if line.strip()]

def get_market_data(tickers):
    data_points = {}
    for ticker in tickers:
        stock = yf.Ticker(ticker)
        hist = stock.history(period="30d") #30 days of historical data
        # hist = stock.history(period="5d") #5 days of historical data till July 2, 2026
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
    # script_dir = os.path.dirname(os.path.abspath(__file__))
    output_dir = os.getenv("OUTPUT_PATH")    
    if use_chinese:
        pdf_filename = os.path.join(output_dir, f"market_recap_{today_str}_zh.pdf")   
    else:
        pdf_filename = os.path.join(output_dir, f"market_recap_{today_str}.pdf")   
    
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
        # font_path = '/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc'
        font_path = os.getenv("CHINESE_FONT_PATH")
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
