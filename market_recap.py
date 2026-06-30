import os
import re
from datetime import datetime
from google import genai
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer
from dotenv import load_dotenv

load_dotenv()  # reads variables from a .env file and sets them in os.environ

def get_prompt_from_file(filepath):
    """Loads the prompt from a markdown file."""
    with open(filepath, 'r', encoding='utf-8') as f:
        return f.read()

def generate_market_recap():
    # 1. Initialize the Gemini API client
    # The client automatically looks for the GEMINI_API_KEY environment variable
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise ValueError(
            "Critical Error: GEMINI_API_KEY environment variable not found."
        )

    # Using the official standard client initialization
    client = genai.Client(api_key=api_key)
    # prompt = "Please recap stock market changes today"

    # 1. Load prompt from file
    prompt_path = "prompts/Market-Recap-Prompt-20260623.md"
    prompt = get_prompt_from_file(prompt_path)
    
    print(f"[{datetime.now()}] Requesting market recap from Gemini API...")

    # Using gemini-2.5-flash as it is fast, highly capable, and cost-effective for text summarization
    response = client.models.generate_content(
        model="gemini-2.5-flash", contents=prompt
    )

    recap_text = response.text

    # 2. Configure PDF Output Destination
    today_str = datetime.now().strftime("%Y-%m-%d")
    script_dir = os.path.dirname(os.path.abspath(__file__))
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

    # Configure typography styles
    styles = getSampleStyleSheet()

    title_style = styles["Heading1"]
    title_style.fontSize = 16
    title_style.leading = 20

    body_style = styles["Normal"]
    body_style.fontSize = 10
    body_style.leading = 15

    story = []

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
    for line in recap_text.split("\n"):
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

    print(f"[{datetime.now()}] Building PDF: {pdf_filename}")
    doc.build(story)
    print(f"[{datetime.now()}] PDF generation complete.")


if __name__ == "__main__":
    generate_market_recap()
