#!/usr/bin/env python3
"""
Download Oregon 2026R1 Senate bills as PDFs, convert to markdown,
and optionally generate AI votes using Claude.
"""

import json
import os
import re
import sys
import time
from pathlib import Path

import httpx
import pymupdf

BASE_URL = "https://olis.oregonlegislature.gov/liz/2026R1"
BILLS_DIR = Path(__file__).parent.parent / "bills"
PDF_DIR = BILLS_DIR / "pdf"
MD_DIR = BILLS_DIR / "md"
DATA_FILE = Path(__file__).parent.parent / "data" / "bills.json"

# Senate bills from the 2026R1 session
SENATE_BILLS = [f"SB{i}" for i in range(1501, 1570)]


def download_pdf(bill_id: str) -> Path | None:
    """Download the Introduced version of a bill PDF."""
    pdf_path = PDF_DIR / f"{bill_id}.pdf"
    if pdf_path.exists():
        print(f"  [skip] {bill_id} PDF already exists")
        return pdf_path

    url = f"{BASE_URL}/Downloads/MeasureDocument/{bill_id}/Introduced"
    print(f"  [download] {bill_id} from {url}")
    try:
        resp = httpx.get(url, follow_redirects=True, timeout=30)
        if resp.status_code == 200 and resp.headers.get("content-type", "").startswith("application/pdf"):
            pdf_path.write_bytes(resp.content)
            return pdf_path
        else:
            print(f"  [warn] {bill_id}: status={resp.status_code}, content-type={resp.headers.get('content-type')}")
            return None
    except Exception as e:
        print(f"  [error] {bill_id}: {e}")
        return None


def pdf_to_markdown(pdf_path: Path) -> str:
    """Extract text from a PDF and return as markdown."""
    doc = pymupdf.open(pdf_path)
    text = ""
    for page in doc:
        text += page.get_text()
    doc.close()

    # Clean up the text
    # Remove excessive whitespace but preserve paragraph breaks
    lines = text.split("\n")
    cleaned = []
    for line in lines:
        stripped = line.strip()
        if stripped:
            cleaned.append(stripped)
        elif cleaned and cleaned[-1] != "":
            cleaned.append("")

    return "\n".join(cleaned)


def extract_bill_title(markdown: str, bill_id: str) -> str:
    """Try to extract a short title/summary from the bill text."""
    lines = [l for l in markdown.split("\n") if l.strip()]
    # Usually the summary/title is in the first few lines after headers
    for line in lines:
        if line.startswith("Relating to"):
            return line.rstrip(";").strip()
        if "relating to" in line.lower():
            # Extract the "Relating to..." part
            match = re.search(r"(relating to .+?)(?:;|$)", line, re.IGNORECASE)
            if match:
                return match.group(1).strip().rstrip(";")
    return f"Senate Bill {bill_id}"


def generate_ai_vote(bill_id: str, markdown: str) -> dict:
    """Use Claude to analyze a bill and vote on it."""
    try:
        import anthropic
    except ImportError:
        print("  [skip] anthropic package not installed, skipping AI vote")
        return {"vote": "unknown", "reasoning": "AI voting not available"}

    client = anthropic.Anthropic()

    # Truncate very long bills
    text = markdown[:8000]

    prompt = f"""You are an AI analyzing Oregon Senate Bill {bill_id}. Read the bill text below and decide how you would vote.

Consider:
- Does this bill benefit the general public?
- Is it fiscally responsible?
- Does it protect individual rights and liberties?
- Is it well-written and clear in its intent?

Bill text:
{text}

Respond in JSON format only:
{{"vote": "yes" or "no", "reasoning": "2-3 sentence explanation of your vote"}}"""

    try:
        response = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=300,
            messages=[{"role": "user", "content": prompt}],
        )
        result = response.content[0].text
        # Parse JSON from response
        json_match = re.search(r"\{.*\}", result, re.DOTALL)
        if json_match:
            return json.loads(json_match.group())
    except Exception as e:
        print(f"  [error] AI vote for {bill_id}: {e}")

    return {"vote": "unknown", "reasoning": "Unable to generate AI vote"}


def main():
    PDF_DIR.mkdir(parents=True, exist_ok=True)
    MD_DIR.mkdir(parents=True, exist_ok=True)
    DATA_FILE.parent.mkdir(parents=True, exist_ok=True)

    skip_ai = "--no-ai" in sys.argv
    bills_data = []

    # Load existing data if available
    existing = {}
    if DATA_FILE.exists():
        for bill in json.loads(DATA_FILE.read_text()):
            existing[bill["id"]] = bill

    for bill_id in SENATE_BILLS:
        print(f"\nProcessing {bill_id}...")

        # Download PDF
        pdf_path = download_pdf(bill_id)
        if not pdf_path:
            print(f"  [skip] Could not download {bill_id}")
            continue

        # Convert to markdown
        md_path = MD_DIR / f"{bill_id}.md"
        if md_path.exists():
            markdown = md_path.read_text()
            print(f"  [skip] {bill_id} markdown already exists")
        else:
            markdown = pdf_to_markdown(pdf_path)
            md_path.write_text(markdown)
            print(f"  [done] Converted {bill_id} to markdown")

        # Extract title
        title = extract_bill_title(markdown, bill_id)

        # AI vote
        if skip_ai:
            vote_data = existing.get(bill_id, {}).get("vote", {"vote": "unknown", "reasoning": ""})
            if isinstance(vote_data, str):
                vote_data = {"vote": vote_data, "reasoning": ""}
        elif bill_id in existing and existing[bill_id].get("vote", {}).get("vote") in ("yes", "no"):
            vote_data = existing[bill_id]["vote"]
            print(f"  [skip] {bill_id} already has AI vote: {vote_data['vote']}")
        else:
            print(f"  [ai] Generating vote for {bill_id}...")
            vote_data = generate_ai_vote(bill_id, markdown)
            print(f"  [done] Vote: {vote_data['vote']}")
            time.sleep(1)  # Rate limiting

        bill_number = bill_id.replace("SB", "SB ")
        bills_data.append({
            "id": bill_id,
            "number": bill_number,
            "title": title,
            "vote": vote_data,
            "url": f"https://olis.oregonlegislature.gov/liz/2026R1/Measures/Overview/{bill_id}",
            "pdf_url": f"{BASE_URL}/Downloads/MeasureDocument/{bill_id}/Introduced",
        })

    # Write JSON data for the website
    DATA_FILE.write_text(json.dumps(bills_data, indent=2))
    print(f"\nDone! Processed {len(bills_data)} bills.")
    print(f"Data written to {DATA_FILE}")


if __name__ == "__main__":
    main()
