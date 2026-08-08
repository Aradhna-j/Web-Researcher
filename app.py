import os
import re
import time
import requests

from bs4 import BeautifulSoup
from ddgs import DDGS
from dotenv import load_dotenv

from google import genai
from google.genai import types

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import (
    SimpleDocTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
)


# ============================================================
# CONFIGURATION
# ============================================================

MODEL_NAME = "gemini-3.6-flash"

# Keep this low because Gemini free-tier RPM can be restrictive.
MAX_AGENT_ITERATIONS = 5

# If Gemini doesn't provide a retry duration, use this fallback.
DEFAULT_RETRY_SECONDS = 65

# Maximum number of retries after a 429.
MAX_API_RETRIES = 4


# ============================================================
# LOAD ENVIRONMENT VARIABLES
# ============================================================

load_dotenv()

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

if not GEMINI_API_KEY:
    raise ValueError(
        "\nGEMINI_API_KEY was not found.\n"
        "Create a .env file in the same folder as app.py and add:\n\n"
        "GEMINI_API_KEY=your_api_key_here\n"
    )


client = genai.Client(
    api_key=GEMINI_API_KEY
)


# ============================================================
# TOOL 1: SEARCH WEB
# ============================================================

def search_web(query: str) -> list[dict]:
    """
    Search DuckDuckGo and return a small number of useful results.
    """

    print(
        f"\n[Tool Execution] Searching web for: '{query}'..."
    )

    results = []

    try:
        ddgs = DDGS()

        search_results = ddgs.text(
            query,
            max_results=4
        )

        for result in search_results:
            results.append(
                {
                    "title": result.get("title", ""),
                    "url": result.get("href", ""),
                    "snippet": result.get("body", ""),
                }
            )

    except Exception as error:
        print(f"[Search Error] {error}")

        return [
            {
                "error": str(error)
            }
        ]

    return results


# ============================================================
# TOOL 2: SCRAPE WEBPAGE
# ============================================================

def scrape_webpage(url: str) -> str:
    """
    Download and extract readable text from a webpage.
    """

    print(
        f"\n[Tool Execution] Fetching page content: {url}..."
    )

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/142.0 Safari/537.36"
        )
    }

    try:
        response = requests.get(
            url,
            headers=headers,
            timeout=10
        )

        response.raise_for_status()

        soup = BeautifulSoup(
            response.text,
            "html.parser"
        )

        # Remove unnecessary webpage elements.
        for element in soup(
            [
                "script",
                "style",
                "nav",
                "footer",
                "header",
                "aside",
                "form",
                "noscript",
            ]
        ):
            element.decompose()

        text = soup.get_text(
            separator=" ",
            strip=True
        )

        # Remove repeated whitespace.
        text = re.sub(
            r"\s+",
            " ",
            text
        )

        if not text:
            return "No readable text was found on this webpage."

        # Keep context smaller to reduce Gemini token usage.
        return text[:4500]

    except Exception as error:
        return f"Failed to scrape webpage: {error}"


# ============================================================
# TOOL MAPPING
# ============================================================

TOOL_FUNCTIONS = {
    "search_web": search_web,
    "scrape_webpage": scrape_webpage,
}


# ============================================================
# GEMINI RATE LIMIT HANDLING
# ============================================================

def get_retry_seconds(error_text: str) -> int:
    """
    Try to extract Google's suggested retry duration
    from the error message.
    """

    patterns = [
        r"retry in ([0-9.]+)s",
        r"'retryDelay': '([0-9]+)s'",
        r'"retryDelay":\s*"([0-9]+)s"',
    ]

    for pattern in patterns:
        match = re.search(
            pattern,
            error_text,
            re.IGNORECASE
        )

        if match:
            try:
                seconds = float(match.group(1))

                # Add a small safety buffer.
                return int(seconds) + 5

            except ValueError:
                pass

    return DEFAULT_RETRY_SECONDS


def send_with_retry(
    chat,
    message,
    max_retries=MAX_API_RETRIES
):
    """
    Send a Gemini message and automatically retry
    when the API returns a temporary rate-limit error.
    """

    for attempt in range(1, max_retries + 1):

        try:
            return chat.send_message(message)

        except Exception as error:
            error_text = str(error)

            is_rate_limit = (
                "429" in error_text
                or "RESOURCE_EXHAUSTED" in error_text
            )

            if not is_rate_limit:
                raise

            if attempt >= max_retries:
                print(
                    "\n[Error] Gemini quota is still unavailable "
                    "after multiple retries."
                )

                raise

            wait_seconds = get_retry_seconds(
                error_text
            )

            print(
                "\n========================================"
            )
            print("GEMINI RATE LIMIT REACHED")
            print("========================================")
            print(
                f"Retry attempt: {attempt}/{max_retries}"
            )
            print(
                f"Waiting {wait_seconds} seconds..."
            )
            print(
                "The program will retry automatically."
            )
            print(
                "========================================"
            )

            time.sleep(wait_seconds)


# ============================================================
# SAVE MARKDOWN
# ============================================================

def save_as_markdown(
    report_text: str,
    filename: str = "research_report.md"
):
    """
    Save final research report as Markdown.
    """

    with open(
        filename,
        "w",
        encoding="utf-8"
    ) as file:
        file.write(report_text)

    print(
        f"[Export Success] Markdown saved to: {filename}"
    )


# ============================================================
# REPORTLAB TEXT CLEANING
# ============================================================

def clean_markdown_text(text: str) -> str:
    """
    Convert basic Markdown formatting into
    ReportLab-compatible inline HTML.
    """

    text = text.replace(
        "&",
        "&amp;"
    )

    text = text.replace(
        "<",
        "&lt;"
    )

    text = text.replace(
        ">",
        "&gt;"
    )

    # Bold Markdown
    text = re.sub(
        r"\*\*(.+?)\*\*",
        r"<b>\1</b>",
        text
    )

    # Inline code
    text = re.sub(
        r"`([^`]+)`",
        r"<font name='Courier'>\1</font>",
        text
    )

    # Markdown links
    text = re.sub(
        r"\[([^\]]+)\]\(([^)]+)\)",
        r'<link href="\2">\1</link>',
        text
    )

    return text


# ============================================================
# PDF GENERATION
# ============================================================

def save_as_pdf(
    report_text: str,
    topic: str,
    pdf_filename: str = "research_report.pdf"
):
    """
    Convert research report into a PDF using ReportLab.
    No WeasyPrint / GTK dependency is required.
    """

    print(
        "\n[Export] Creating PDF report..."
    )

    document = SimpleDocTemplate(
        pdf_filename,
        pagesize=A4,
        rightMargin=18 * mm,
        leftMargin=18 * mm,
        topMargin=18 * mm,
        bottomMargin=18 * mm,
        title=topic,
        author="AI Web Research Agent",
    )

    sample_styles = getSampleStyleSheet()

    title_style = ParagraphStyle(
        "CustomTitle",
        parent=sample_styles["Title"],
        fontName="Helvetica-Bold",
        fontSize=20,
        leading=25,
        alignment=TA_CENTER,
        textColor=colors.HexColor("#0F172A"),
        spaceAfter=14,
    )

    subtitle_style = ParagraphStyle(
        "Subtitle",
        parent=sample_styles["Normal"],
        fontName="Helvetica",
        fontSize=9,
        leading=12,
        alignment=TA_CENTER,
        textColor=colors.HexColor("#64748B"),
        spaceAfter=20,
    )

    heading1_style = ParagraphStyle(
        "Heading1Custom",
        parent=sample_styles["Heading1"],
        fontName="Helvetica-Bold",
        fontSize=15,
        leading=19,
        textColor=colors.HexColor("#1D4ED8"),
        spaceBefore=13,
        spaceAfter=8,
    )

    heading2_style = ParagraphStyle(
        "Heading2Custom",
        parent=sample_styles["Heading2"],
        fontName="Helvetica-Bold",
        fontSize=12,
        leading=16,
        textColor=colors.HexColor("#1E3A8A"),
        spaceBefore=10,
        spaceAfter=6,
    )

    heading3_style = ParagraphStyle(
        "Heading3Custom",
        parent=sample_styles["Heading3"],
        fontName="Helvetica-Bold",
        fontSize=10.5,
        leading=14,
        textColor=colors.HexColor("#334155"),
        spaceBefore=8,
        spaceAfter=5,
    )

    body_style = ParagraphStyle(
        "BodyCustom",
        parent=sample_styles["BodyText"],
        fontName="Helvetica",
        fontSize=9.5,
        leading=14,
        textColor=colors.HexColor("#1E293B"),
        spaceAfter=7,
    )

    bullet_style = ParagraphStyle(
        "BulletCustom",
        parent=body_style,
        leftIndent=15,
        firstLineIndent=-8,
        spaceAfter=5,
    )

    code_style = ParagraphStyle(
        "CodeCustom",
        parent=body_style,
        fontName="Courier",
        fontSize=8,
        leading=11,
        leftIndent=10,
        rightIndent=10,
        backColor=colors.HexColor("#F1F5F9"),
        borderPadding=8,
        spaceBefore=5,
        spaceAfter=8,
    )

    story = []

    # --------------------------------------------------------
    # PDF Header
    # --------------------------------------------------------

    story.append(
        Paragraph(
            "AI WEB RESEARCH REPORT",
            subtitle_style
        )
    )

    story.append(
        Paragraph(
            clean_markdown_text(topic),
            title_style
        )
    )

    story.append(
        Paragraph(
            "Generated by Gemini-powered autonomous web research agent",
            subtitle_style
        )
    )

    story.append(
        Spacer(
            1,
            8
        )
    )

    # --------------------------------------------------------
    # Parse report
    # --------------------------------------------------------

    lines = report_text.splitlines()

    inside_code_block = False
    code_lines = []

    table_rows = []


    def flush_table():
        """
        Add accumulated Markdown table rows to the PDF.
        """

        nonlocal table_rows

        if not table_rows:
            return

        maximum_columns = max(
            len(row)
            for row in table_rows
        )

        normalized_rows = []

        for row in table_rows:

            while len(row) < maximum_columns:
                row.append("")

            normalized_rows.append(
                [
                    Paragraph(
                        clean_markdown_text(cell),
                        body_style
                    )
                    for cell in row
                ]
            )

        available_width = (
            A4[0]
            - document.leftMargin
            - document.rightMargin
        )

        column_width = (
            available_width / maximum_columns
        )

        table = Table(
            normalized_rows,
            colWidths=[
                column_width
            ] * maximum_columns,
            repeatRows=1,
        )

        table_style_commands = [
            (
                "GRID",
                (0, 0),
                (-1, -1),
                0.5,
                colors.HexColor("#CBD5E1"),
            ),
            (
                "VALIGN",
                (0, 0),
                (-1, -1),
                "TOP",
            ),
            (
                "LEFTPADDING",
                (0, 0),
                (-1, -1),
                6,
            ),
            (
                "RIGHTPADDING",
                (0, 0),
                (-1, -1),
                6,
            ),
            (
                "TOPPADDING",
                (0, 0),
                (-1, -1),
                6,
            ),
            (
                "BOTTOMPADDING",
                (0, 0),
                (-1, -1),
                6,
            ),
        ]

        if len(normalized_rows) > 0:
            table_style_commands.extend(
                [
                    (
                        "BACKGROUND",
                        (0, 0),
                        (-1, 0),
                        colors.HexColor("#0F172A"),
                    ),
                    (
                        "TEXTCOLOR",
                        (0, 0),
                        (-1, 0),
                        colors.white,
                    ),
                    (
                        "FONTNAME",
                        (0, 0),
                        (-1, 0),
                        "Helvetica-Bold",
                    ),
                ]
            )

        table.setStyle(
            TableStyle(
                table_style_commands
            )
        )

        story.append(table)

        story.append(
            Spacer(
                1,
                10
            )
        )

        table_rows = []


    for raw_line in lines:

        line = raw_line.strip()

        # ----------------------------------------------------
        # Code fences
        # ----------------------------------------------------

        if line.startswith("```"):

            flush_table()

            if not inside_code_block:

                inside_code_block = True
                code_lines = []

            else:

                inside_code_block = False

                code_text = "<br/>".join(
                    clean_markdown_text(code_line)
                    for code_line in code_lines
                )

                story.append(
                    Paragraph(
                        code_text,
                        code_style
                    )
                )

            continue


        if inside_code_block:
            code_lines.append(
                raw_line
            )
            continue


        # ----------------------------------------------------
        # Markdown tables
        # ----------------------------------------------------

        if (
            line.startswith("|")
            and line.endswith("|")
        ):

            cells = [
                cell.strip()
                for cell in line.strip("|").split("|")
            ]

            # Ignore separator:
            # | --- | --- |
            is_separator = all(
                re.match(
                    r"^:?-{3,}:?$",
                    cell.replace(" ", "")
                )
                for cell in cells
            )

            if not is_separator:
                table_rows.append(
                    cells
                )

            continue

        else:
            flush_table()


        # ----------------------------------------------------
        # Blank lines
        # ----------------------------------------------------

        if not line:

            story.append(
                Spacer(
                    1,
                    3
                )
            )

            continue


        # ----------------------------------------------------
        # Headings
        # ----------------------------------------------------

        if line.startswith("### "):

            story.append(
                Paragraph(
                    clean_markdown_text(
                        line[4:]
                    ),
                    heading3_style
                )
            )

            continue


        if line.startswith("## "):

            story.append(
                Paragraph(
                    clean_markdown_text(
                        line[3:]
                    ),
                    heading2_style
                )
            )

            continue


        if line.startswith("# "):

            story.append(
                Paragraph(
                    clean_markdown_text(
                        line[2:]
                    ),
                    heading1_style
                )
            )

            continue


        # ----------------------------------------------------
        # Bullet points
        # ----------------------------------------------------

        if (
            line.startswith("- ")
            or line.startswith("* ")
        ):

            story.append(
                Paragraph(
                    "• "
                    + clean_markdown_text(
                        line[2:]
                    ),
                    bullet_style
                )
            )

            continue


        # ----------------------------------------------------
        # Numbered list
        # ----------------------------------------------------

        numbered_match = re.match(
            r"^(\d+)\.\s+(.+)$",
            line
        )

        if numbered_match:

            number = numbered_match.group(1)
            content = numbered_match.group(2)

            story.append(
                Paragraph(
                    f"{number}. "
                    + clean_markdown_text(content),
                    bullet_style
                )
            )

            continue


        # ----------------------------------------------------
        # Normal paragraph
        # ----------------------------------------------------

        story.append(
            Paragraph(
                clean_markdown_text(line),
                body_style
            )
        )


    flush_table()

    # In case a code block wasn't closed.
    if inside_code_block and code_lines:

        code_text = "<br/>".join(
            clean_markdown_text(code_line)
            for code_line in code_lines
        )

        story.append(
            Paragraph(
                code_text,
                code_style
            )
        )


    document.build(
        story
    )

    print(
        f"[Export Success] PDF saved to: {pdf_filename}"
    )


# ============================================================
# RESEARCH AGENT
# ============================================================

def run_research_agent(
    research_topic: str
):

    print(
        "\n============================================"
    )
    print("AI WEB RESEARCH AGENT")
    print(
        "============================================"
    )
    print(
        f"Research topic: {research_topic}"
    )


    system_instruction = """
You are an autonomous web research agent.

Your task is to research the user's topic using the provided
search_web and scrape_webpage tools.

IMPORTANT RULES:

1. Be efficient with tool usage.
2. Do not repeatedly search similar queries.
3. Usually perform only 1 or 2 high-quality web searches.
4. Scrape only the 2 or 3 most useful sources.
5. After collecting enough evidence, stop using tools.
6. Produce the final report instead of continuing unnecessary research.
7. Never invent URLs, sources, statistics, qualifications, or facts.
8. Clearly distinguish source-backed facts from recommendations.
9. Prefer authoritative and recent sources when available.

Produce a useful Markdown report with appropriate sections such as:

# Executive Summary

# Key Findings

# Detailed Analysis

# Minimum Requirements

# Skills Needed

# Recommended Learning Path

# Portfolio / Project Requirements

# Job Readiness

# Conclusion

# Sources

Adapt the headings to the user's actual research topic.

For every source, provide:
- Source title
- Website/domain
- URL

The goal is quality research with minimal API requests.
"""


    print(
        f"\n[Agent] Creating Gemini chat using {MODEL_NAME}..."
    )


    chat = client.chats.create(
        model=MODEL_NAME,

        config=types.GenerateContentConfig(
            system_instruction=system_instruction,

            tools=[
                search_web,
                scrape_webpage
            ],
        ),
    )


    initial_prompt = f"""
Research this topic:

{research_topic}

Use web tools efficiently.

Do not perform repetitive searches.

Generally use no more than:
- 2 web searches
- 3 webpage scrapes

Once you have enough reliable information,
write the final research report.
"""


    print(
        "\n[Agent] Sending research request to Gemini..."
    )


    response = send_with_retry(
        chat,
        initial_prompt
    )


    # ========================================================
    # AGENT TOOL LOOP
    # ========================================================

    iteration = 0


    while (
        response.function_calls
        and iteration < MAX_AGENT_ITERATIONS
    ):

        iteration += 1

        print(
            f"\n[Agent] Tool cycle "
            f"{iteration}/{MAX_AGENT_ITERATIONS}"
        )

        function_responses = []


        for function_call in response.function_calls:

            function_name = function_call.name

            function_args = dict(
                function_call.args
            )


            print(
                f"[Agent] Gemini requested: "
                f"{function_name}"
            )


            if function_name not in TOOL_FUNCTIONS:

                tool_result = (
                    f"Error: Unknown tool "
                    f"'{function_name}'."
                )

            else:

                try:
                    tool_result = (
                        TOOL_FUNCTIONS[
                            function_name
                        ](
                            **function_args
                        )
                    )

                except Exception as error:

                    tool_result = (
                        f"Tool execution failed: "
                        f"{error}"
                    )


            function_response = (
                types.Part.from_function_response(
                    name=function_name,

                    response={
                        "result": tool_result
                    },
                )
            )


            function_responses.append(
                function_response
            )


        response = send_with_retry(
            chat,
            function_responses
        )


    # ========================================================
    # FORCE FINAL RESPONSE IF TOOL LIMIT REACHED
    # ========================================================

    if response.function_calls:

        print(
            "\n[Agent] Maximum research cycles reached."
        )

        print(
            "[Agent] Requesting final report from "
            "existing research..."
        )


        response = send_with_retry(
            chat,
            """
Stop using tools now.

Using only the information already collected,
produce the complete final Markdown research report.

Do not request any additional searches or webpage scrapes.
"""
        )


    # ========================================================
    # FINAL REPORT
    # ========================================================

    report_text = response.text


    if not report_text:

        raise RuntimeError(
            "Gemini finished without returning "
            "a final text report."
        )


    print(
        "\n============================================"
    )
    print("RESEARCH COMPLETE")
    print(
        "============================================\n"
    )


    print(
        report_text
    )


    # ========================================================
    # SAVE OUTPUTS
    # ========================================================

    save_as_markdown(
        report_text,
        "research_report.md"
    )


    try:

        save_as_pdf(
            report_text,
            research_topic,
            "research_report.pdf"
        )

    except Exception as error:

        print(
            f"\n[PDF Error] Could not create PDF: {error}"
        )

        print(
            "Markdown report was still saved successfully."
        )


    print(
        "\n============================================"
    )
    print("OUTPUT FILES")
    print(
        "============================================"
    )
    print("research_report.md")
    print("research_report.pdf")
    print(
        "============================================"
    )


# ============================================================
# MAIN
# ============================================================

if __name__ == "__main__":

    print(
        "\n============================================"
    )
    print("AI Web Research Agent")
    print(
        "============================================"
    )


    topic = input(
        "\nEnter research topic: "
    ).strip()


    if not topic:

        topic = (
            "Minimum skills and requirements needed "
            "to get an entry-level job in Agentic AI"
        )


    try:

        run_research_agent(
            topic
        )

    except KeyboardInterrupt:

        print(
            "\n\nProgram stopped by user."
        )

    except Exception as error:

        print(
            "\n============================================"
        )
        print("APPLICATION ERROR")
        print(
            "============================================"
        )
        print(error)
        print(
            "============================================"
        )