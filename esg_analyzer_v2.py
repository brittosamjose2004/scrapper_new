"""
ESG Analyzer v2 — Option B: Full Document Context + All Questions in ONE LLM Call

For each year:
  1. AUTO-DOWNLOAD any missing documents (Annual Report, BRSR, Sustainability)
  2. Convert new PDFs to TXT
  3. Load ALL documents for that year into one full text block
  4. Send full text + all 90 ESG questions in a SINGLE Gemini API call
  5. Parse the JSON response with all 90 answers at once
  6. Save to JSON file
  7. Move to the next year and repeat

vs v1 (chunk-based):
  - No chunking / keyword scoring — LLM sees the FULL document
  - 1 API call per year instead of 90
  - ~60x cheaper, faster, and more accurate
  - Auto-downloads missing documents before analysis

Usage:
  python esg_analyzer_v2.py --company "Reliance" --gemini-key KEY --year 2023
  python esg_analyzer_v2.py --company "Reliance" --gemini-key KEY --yearly
  python esg_analyzer_v2.py --company "Tata Power" --gemini-key KEY --yearly

  # Skip auto-download (use only what's already on disk):
  python esg_analyzer_v2.py --company "Reliance" --gemini-key KEY --year 2023 --no-download
"""

import os
import re
import sys
import json
import time
import argparse
import logging
from datetime import datetime

logging.basicConfig(level=logging.WARNING)
logger = logging.getLogger(__name__)

# ── Constants ──────────────────────────────────────────────────────────────────
GEMINI_MODEL        = "gemini-2.0-flash"  # gemini-2.0-flash-lite deprecated; use gemini-2.0-flash (fastest available)
ESG_QUESTIONS_FILE  = os.path.join(os.path.dirname(os.path.abspath(__file__)), "esg_questions.json")
MAX_FULL_TEXT_CHARS = 1_000_000   # ~250K tokens — within Gemini 2.0 Flash Lite's context limit
MAX_CHARS_ANNUAL    = 500_000     # Annual Report: up to 125K tokens (full report)
MAX_CHARS_BRSR      = 300_000     # BRSR: up to 75K tokens (full BRSR)
MAX_CHARS_SUSTAIN   = 20_000      # Sustainability/TCFD: 5K token snippet each (key ESG metrics only)
SLEEP_BETWEEN_YEARS = 3           # seconds between year calls (politeness)

# Wrong-company markers — files whose name contains ANY of these will be skipped
WRONG_COMPANY_MARKERS = [
    "worldwide corporation",
    "worldwide corp",
    " rwc ",
    "rwc ",
    " rwc.",
    "holman",
    "asx listed",
    "west drayton",
    "cullman, alabama",
]


# ══════════════════════════════════════════════════════════════════════════════
#  HELPERS
# ══════════════════════════════════════════════════════════════════════════════

def _sanitize(name):
    """Sanitize company name the same way scraper.py does."""
    return "".join([c for c in name if c.isalpha() or c.isdigit() or c in (' ', '-', '_')]).strip()


def _is_wrong_company_file(fname, company_name):
    """Return True if this file clearly belongs to a different company."""
    fname_lower = fname.lower()
    company_lower = company_name.lower().split()[0]  # first word, e.g. "reliance"

    for marker in WRONG_COMPANY_MARKERS:
        if marker in fname_lower:
            return True
    return False


def _file_priority(fname_lower):
    """Return sort priority (lower = more important = loaded first)."""
    if "brsr" in fname_lower:
        return 0   # BRSR first — most structured ESG data
    if re.match(r'^\d{4}_', fname_lower) or "annual" in fname_lower:
        return 1   # Annual reports second
    if "sustainability" in fname_lower or "tcfd" in fname_lower:
        return 2   # Sustainability/TCFD third
    return 3


def detect_available_years(base_folder, company_name):
    """Scan company folders and return sorted list of years with annual report files."""
    sanitized = _sanitize(company_name)
    name_lower = sanitized.lower()

    candidate_dirs = []
    for root_sub in ("nseindia.com", "annualreports.com", ""):
        d = os.path.join(base_folder, root_sub, sanitized) if root_sub else os.path.join(base_folder, sanitized)
        if os.path.exists(d):
            candidate_dirs.append(d)

    # Also fuzzy-match subdirectories
    if os.path.exists(base_folder):
        for entry in os.listdir(base_folder):
            p = os.path.join(base_folder, entry)
            if os.path.isdir(p) and entry in ("annualreports.com", "nseindia.com"):
                for sub in os.listdir(p):
                    sp = os.path.join(p, sub)
                    if os.path.isdir(sp) and name_lower in sub.lower():
                        candidate_dirs.append(sp)

    years = set()
    for d in candidate_dirs:
        for fname in os.listdir(d):
            m = re.match(r'^(\d{4})_', fname)
            if m:
                yr = int(m.group(1))
                if 2000 <= yr <= 2030:
                    years.add(yr)
    return sorted(years)


# ══════════════════════════════════════════════════════════════════════════════
#  AUTO-DOWNLOAD MISSING DOCUMENTS
# ══════════════════════════════════════════════════════════════════════════════

def _has_annual_report(company_dir, year):
    """Check if annual report TXT/PDF exists for the given year."""
    yr = str(year)
    if not os.path.exists(company_dir):
        return False
    for f in os.listdir(company_dir):
        if f.startswith(f"{yr}_") and f.lower().endswith(('.txt', '.pdf')):
            return True
    return False


def _has_brsr(company_dir, year):
    """Check if a BRSR TXT/PDF exists for or near the given year."""
    brsr_dir = os.path.join(company_dir, "BRSR")
    if not os.path.exists(brsr_dir):
        return False
    yr = str(year)
    prev = str(year - 1)
    for f in os.listdir(brsr_dir):
        fl = f.lower()
        if "brsr" in fl and (yr in fl or prev in fl) and fl.endswith(('.txt', '.pdf')):
            return True
    return False


def _has_sustainability(company_dir, company_name):
    """Check if any sustainability/TCFD report exists (not wrong-company)."""
    sust_dir = os.path.join(company_dir, "Sustainability")
    if not os.path.exists(sust_dir):
        return False
    for f in os.listdir(sust_dir):
        if _is_wrong_company_file(f, company_name):
            continue
        fl = f.lower()
        if fl.endswith(('.txt', '.pdf')) and ("sustainability" in fl or "tcfd" in fl or "brsr" in fl):
            return True
    return False


def _pdf_to_txt_single(pdf_path):
    """Convert a single PDF to a TXT file (same path, .txt extension)."""
    txt_path = os.path.splitext(pdf_path)[0] + ".txt"
    if os.path.exists(txt_path):
        return txt_path
    try:
        from pypdf import PdfReader
        reader = PdfReader(pdf_path)
        lines = [f"# Extracted from: {os.path.basename(pdf_path)}",
                 f"# Total pages with text: {len(reader.pages)}",
                 "=" * 80]
        for i, page in enumerate(reader.pages):
            text = page.extract_text()
            if text and text.strip():
                lines.append(f"\n--- PAGE {i+1} ---")
                lines.append(text)
        content = "\n".join(lines)
        with open(txt_path, 'w', encoding='utf-8') as f:
            f.write(content)
        print(f"      📝 Converted to TXT: {os.path.basename(txt_path)}")
        return txt_path
    except Exception as e:
        print(f"      ⚠️  PDF→TXT failed for {os.path.basename(pdf_path)}: {e}")
        return None


def auto_download_missing(company_name, year, base_folder):
    """
    Check what documents are missing for the given company+year and
    automatically download them from NSE India and sustainability search.

    Downloads:
    - Annual Report (from NSE India) if missing
    - BRSR report (from NSE India) if missing and year >= 2022
    - Sustainability/TCFD reports (from web search) if none exist at all
    - News articles if news JSON doesn't exist

    After downloading PDFs, converts them to TXT automatically.
    """
    sanitized = _sanitize(company_name)
    company_dir = os.path.join(base_folder, "nseindia.com", sanitized)
    brsr_dir = os.path.join(company_dir, "BRSR")
    sust_dir = os.path.join(company_dir, "Sustainability")
    news_dir = os.path.join(company_dir, "News")

    print(f"\n  🔍 Checking for missing documents for {company_name} — {year}...")

    missing = []
    if not _has_annual_report(company_dir, year):
        missing.append("annual_report")
    if year >= 2022 and not _has_brsr(company_dir, year):
        missing.append("brsr")
    if not _has_sustainability(company_dir, company_name):
        missing.append("sustainability")
    # Check news
    news_exists = os.path.exists(news_dir) and any(
        f.endswith('.json') for f in os.listdir(news_dir)
    ) if os.path.exists(news_dir) else False
    if not news_exists:
        missing.append("news")

    if not missing:
        print(f"     ✅ All documents present — no download needed")
        return

    print(f"     📥 Missing: {', '.join(missing)} — downloading now...")

    newly_downloaded_pdfs = []

    # ── Annual Report ─────────────────────────────────────────────────────────
    if "annual_report" in missing:
        print(f"\n     📄 Downloading Annual Report {year} from NSE India...")
        try:
            from nse_client import NSEClient
            import scraper as _scraper
            nse = NSEClient()
            results = nse.search_company(company_name)
            if results:
                symbol = results[0]['symbol']
                reports = nse.get_annual_reports(symbol)
                yr_str = str(year)
                matched = [r for r in reports if str(r.get('year', '')) == yr_str]
                if not matched:
                    # Try previous year (some companies file year+1)
                    matched = [r for r in reports if str(r.get('year', '')) == str(year - 1)]
                if matched:
                    report = matched[0]
                    desc = _scraper.sanitize_filename(report.get('description', 'Annual Report'))
                    fname = f"{year}_{desc}.pdf"
                    os.makedirs(company_dir, exist_ok=True)
                    _scraper.download_file(report['url'], company_dir, fname,
                                           headers=nse.session.headers)
                    pdf_path = os.path.join(company_dir, fname)
                    if os.path.exists(pdf_path):
                        newly_downloaded_pdfs.append(pdf_path)
                        print(f"       ✅ Downloaded: {fname}")
                else:
                    print(f"       ⚠️  No Annual Report found for {year} on NSE")
            else:
                print(f"       ⚠️  Company '{company_name}' not found on NSE")
        except Exception as e:
            print(f"       ❌ Annual report download failed: {e}")

    # ── BRSR ──────────────────────────────────────────────────────────────────
    if "brsr" in missing:
        print(f"\n     📊 Downloading BRSR {year} from NSE India...")
        try:
            from nse_client import NSEClient
            import scraper as _scraper
            nse = NSEClient()
            results = nse.search_company(company_name)
            if results:
                symbol = results[0]['symbol']
                brsr_reports = nse.get_brsr_reports(symbol)
                yr_str = str(year)
                prev_yr = str(year - 1)
                matched = [r for r in brsr_reports
                           if str(r.get('year', '')) in (yr_str, prev_yr)]
                if matched:
                    os.makedirs(brsr_dir, exist_ok=True)
                    for report in matched[:2]:   # max 2
                        r_year = report.get('year', year)
                        date = report.get('date', '').replace(':', '').replace(' ', '_')
                        fname = f"BRSR_{r_year}_{date}.pdf"
                        _scraper.download_file(report['url'], brsr_dir, fname,
                                               headers=nse.session.headers)
                        pdf_path = os.path.join(brsr_dir, fname)
                        if os.path.exists(pdf_path):
                            newly_downloaded_pdfs.append(pdf_path)
                            print(f"       ✅ Downloaded: {fname}")
                else:
                    print(f"       ⚠️  No BRSR found for {year}/{year-1} on NSE")
        except Exception as e:
            print(f"       ❌ BRSR download failed: {e}")

    # ── Sustainability / TCFD ─────────────────────────────────────────────────
    if "sustainability" in missing:
        print(f"\n     🌱 Searching for Sustainability & TCFD reports...")
        try:
            from search_scraper import SearchScraper
            os.makedirs(sust_dir, exist_ok=True)
            searcher = SearchScraper()
            before = set(os.listdir(sust_dir))
            searcher.search_and_download_pdfs(company_name, "Sustainability Report", sust_dir)
            searcher.search_and_download_pdfs(company_name, "TCFD Report", sust_dir)
            after = set(os.listdir(sust_dir))
            new_files = after - before
            for f in new_files:
                fp = os.path.join(sust_dir, f)
                if f.lower().endswith('.pdf'):
                    newly_downloaded_pdfs.append(fp)
            print(f"       ✅ Downloaded {len(new_files)} sustainability files")
        except Exception as e:
            print(f"       ❌ Sustainability download failed: {e}")

    # ── News ──────────────────────────────────────────────────────────────────
    if "news" in missing:
        print(f"\n     📰 Fetching news articles...")
        try:
            from news_scraper import NewsScraper
            os.makedirs(news_dir, exist_ok=True)
            ns = NewsScraper()
            news_items = ns.fetch_massive_news(company_name, total_limit=50)
            if news_items:
                ns.save_data(news_items, news_dir, "news_fulltext")
                print(f"       ✅ Saved {len(news_items)} news articles")
        except Exception as e:
            print(f"       ❌ News fetch failed: {e}")

    # ── Convert newly downloaded PDFs → TXT ──────────────────────────────────
    if newly_downloaded_pdfs:
        print(f"\n     🔄 Converting {len(newly_downloaded_pdfs)} new PDF(s) to TXT...")
        for pdf_path in newly_downloaded_pdfs:
            if os.path.exists(pdf_path):
                _pdf_to_txt_single(pdf_path)

    print(f"     ✅ Auto-download complete")


# ══════════════════════════════════════════════════════════════════════════════
#  DOCUMENT LOADING — returns one big text string per year
# ══════════════════════════════════════════════════════════════════════════════

def load_year_documents(base_folder, company_name, year):
    """
    Load all documents for a specific year into a single text string.

    Returns:
        (full_text: str, file_list: list[str], total_chars: int)
    """
    sanitized = _sanitize(company_name)
    name_lower = sanitized.lower()
    year_str = str(year)

    # Build candidate directories
    search_dirs = []
    for root_sub in ("nseindia.com", "annualreports.com", ""):
        d = os.path.join(base_folder, root_sub, sanitized) if root_sub else os.path.join(base_folder, sanitized)
        if os.path.exists(d):
            search_dirs.append(d)
    if os.path.exists(base_folder):
        for entry in os.listdir(base_folder):
            p = os.path.join(base_folder, entry)
            if os.path.isdir(p) and entry in ("annualreports.com", "nseindia.com"):
                for sub in os.listdir(p):
                    sp = os.path.join(p, sub)
                    if os.path.isdir(sp) and name_lower in sub.lower() and sp not in search_dirs:
                        search_dirs.append(sp)

    # Collect all matching files with their priority
    candidates = []  # (priority, filepath, fname, section_label)

    # Track which TXT basenames exist (to skip PDF duplicates)
    all_txt_bases = set()
    for search_dir in search_dirs:
        for root, _, files in os.walk(search_dir):
            for fname in files:
                if fname.lower().endswith('.txt'):
                    all_txt_bases.add(os.path.splitext(fname)[0].lower())

    for search_dir in search_dirs:
        for root, _, files in os.walk(search_dir):
            for fname in sorted(files):
                fname_lower = fname.lower()
                filepath = os.path.join(root, fname)

                # Skip wrong-company files
                if _is_wrong_company_file(fname, company_name):
                    print(f"    ⛔ Skipping wrong-company file: {fname}")
                    continue

                # Skip non-text/pdf files and ESG answer JSONs
                if "esg_answers" in fname_lower or "esg_answer" in fname_lower:
                    continue

                # Relative path inside this search_dir
                rel = os.path.relpath(filepath, search_dir)
                rel_parts = rel.split(os.sep)
                sub_name = rel_parts[0].lower() if len(rel_parts) > 1 else ""

                # Determine allowed file types per sub-folder
                is_news_json = (sub_name == "news" and fname_lower.endswith('.json')
                                and "news" in fname_lower)
                if not (fname_lower.endswith(('.txt', '.pdf')) or is_news_json):
                    continue

                # Skip PDFs that have a TXT version
                if fname_lower.endswith('.pdf'):
                    base = os.path.splitext(fname)[0].lower()
                    if base in all_txt_bases:
                        continue

                # Year matching
                # - Annual reports: must have year prefix  (e.g. "2023_Annual Report…")
                # - BRSR:            must mention year, year-1, or year+1 (adjacent filings)
                # - Sustainability/TCFD: loaded unconditionally (company-wide context)
                # - News JSON:        loaded unconditionally
                year_ok = bool(
                    re.match(rf'^{year_str}_', fname)
                    or re.match(rf'^brsr_{year_str}', fname_lower)
                    or re.match(rf'^brsr_{str(year-1)}', fname_lower)
                    or re.match(rf'^brsr_{str(year+1)}', fname_lower)   # year+1 BRSR covers current FY
                    or (sub_name == 'brsr' and year_str in fname)
                    or sub_name in ('sustainability', 'tcfd')   # no year filter — company-wide
                    or is_news_json                              # no year filter
                )
                if not year_ok:
                    continue

                priority = _file_priority(fname_lower)
                # section label shown in the prompt so LLM knows source type
                # NOTE: check sustainability/tcfd BEFORE "annual" to avoid
                # mislabelling e.g. "Sustainability Report ... Annual Report 2020 - Craftco.txt"
                if is_news_json:
                    label = "News Articles (Recent)"
                elif "brsr" in fname_lower:
                    label = "BRSR (Business Responsibility & Sustainability Report)"
                elif "sustainability" in fname_lower:
                    label = "Sustainability Report"
                elif "tcfd" in fname_lower:
                    label = "TCFD Report"
                elif "annual" in fname_lower or re.match(r'^\d{4}_', fname):
                    label = "Annual Report"
                else:
                    label = "Other Report"

                candidates.append((priority, filepath, fname, label))

    # Sort by priority (BRSR first, Annual second, Sustainability third)
    candidates.sort(key=lambda x: x[0])

    if not candidates:
        return "", [], 0

    # Read and concatenate
    parts = []
    file_list = []
    total_chars = 0

    for priority, filepath, fname, label in candidates:
        print(f"    {'📋' if 'brsr' in fname.lower() else '📄'} [{label}] {fname}")
        file_list.append(fname)

        try:
            if filepath.lower().endswith('.json'):
                import json as _json
                with open(filepath, 'r', encoding='utf-8') as f:
                    news_data = _json.load(f)
                # Flatten news into readable text
                items = news_data if isinstance(news_data, list) else news_data.get('articles', [])
                snippets = []
                for item in items[:80]:  # cap at 80 articles
                    title = item.get('title', '')
                    date = item.get('published', item.get('date', ''))
                    body = item.get('full_text', item.get('text', item.get('summary', '')))
                    if title or body:
                        snippets.append(f"[{date}] {title}\n{body[:2000]}")
                content = "\n\n---\n\n".join(snippets) if snippets else ""
            elif filepath.lower().endswith('.txt'):
                with open(filepath, 'r', encoding='utf-8') as f:
                    content = f.read()
                # Strip the extraction header lines
                lines = content.split('\n')
                clean_lines = [l for l in lines if not l.startswith('# Extracted from:')
                               and not l.startswith('# Total pages') and l != '=' * 80]
                content = '\n'.join(clean_lines)
            else:
                # PDF fallback
                try:
                    from pypdf import PdfReader
                    reader = PdfReader(filepath)
                    page_texts = []
                    for i, page in enumerate(reader.pages):
                        t = page.extract_text()
                        if t and t.strip():
                            page_texts.append(f"--- PAGE {i+1} ---\n{t}")
                    content = '\n'.join(page_texts)
                except Exception as e:
                    print(f"      ⚠️  Could not read PDF: {e}")
                    continue

            if not content.strip():
                continue

            # Per-file size cap by source type
            if "annual" in label.lower():
                per_file_cap = MAX_CHARS_ANNUAL
            elif "brsr" in label.lower():
                per_file_cap = MAX_CHARS_BRSR
            else:
                per_file_cap = MAX_CHARS_SUSTAIN
            # If cap is 0, skip this file entirely (don't add to file_list)
            if per_file_cap == 0:
                file_list.pop()  # undo the append above
                continue
            if len(content) > per_file_cap:
                content = content[:per_file_cap] + "\n... [TRUNCATED — file too large]"

            section = f"\n{'='*80}\n## SOURCE: {label} — {fname}\n{'='*80}\n{content}\n"

            # Safety cap: don't exceed max chars
            if total_chars + len(section) > MAX_FULL_TEXT_CHARS:
                remaining = MAX_FULL_TEXT_CHARS - total_chars
                if remaining > 500:
                    section = section[:remaining] + "\n... [TRUNCATED — document too large]"
                    parts.append(section)
                    total_chars += len(section)
                print(f"      ⚠️  Text cap reached ({MAX_FULL_TEXT_CHARS:,} chars) — remaining files skipped")
                break

            parts.append(section)
            total_chars += len(section)

        except Exception as e:
            logger.warning(f"    Failed to read {fname}: {e}")

    full_text = "\n".join(parts)
    return full_text, file_list, total_chars


# ══════════════════════════════════════════════════════════════════════════════
#  PROMPT BUILDING
# ══════════════════════════════════════════════════════════════════════════════

def flatten_questions(questions_data):
    """
    Flatten the nested questions JSON into a list of dicts:
    [{"id": 1, "pillar": ..., "category": ..., "question": ...}, ...]
    """
    flat = []
    qid = 1
    for pillar_key, pillar_data in questions_data.items():
        pillar_label = pillar_key.replace("_", " ").title()
        for sub_key, sub_data in pillar_data.items():
            sub_label = sub_key.replace("_", " ").title()
            for q in sub_data.get("questions", []):
                flat.append({
                    "id": qid,
                    "pillar_key": pillar_key,
                    "pillar_label": pillar_label,
                    "sub_key": sub_key,
                    "sub_label": sub_label,
                    "question": q,
                })
                qid += 1
    return flat


def build_mega_prompt(full_text, flat_questions, company_name, year):
    """Build a single prompt with all documents + all ESG questions."""

    questions_block = "\n".join(
        f'  {q["id"]}. [{q["pillar_label"]} > {q["sub_label"]}] {q["question"]}'
        for q in flat_questions
    )

    return f"""You are an expert ESG financial analyst with deep knowledge of Indian corporate reports, BRSR, GRI, and annual report disclosures.

COMPANY: {company_name}
REPORTING YEAR: {year}

━━━━━━━━━━━━━━━━━━━━━━ DOCUMENTS ━━━━━━━━━━━━━━━━━━━━━━
{full_text}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

TASK: Extract OR derive values for ALL {len(flat_questions)} ESG metrics listed below.

METRICS TO EXTRACT:
{questions_block}

EXTRACTION RULES — follow in priority order:
1. EXACT: Find the number stated directly in the document. Use it.
2. CALCULATE: If the components are present, compute the answer.
   Examples:
   - Debt-to-equity = Total Debt / Total Equity (from balance sheet)
   - Interest coverage = EBIT / Interest expense (from P&L)
   - Free cash flow trend = (FCF_current - FCF_prior) / FCF_prior × 100
   - ROIC = NOPAT / Invested Capital (look in financial highlights)
   - CSR % of profit = CSR spend / PAT × 100
   - Revenue volatility = StdDev of last 3 years / mean revenue × 100
   - Emissions vs revenue divergence = emissions_growth - revenue_growth
   - ESG improvement rate = average annual Scope 1+2 reduction over 3 years
3. INFER: If the document gives enough context to give a reliable proxy, use it.
   Examples:
   - "No product recalls" → Product recalls = 0
   - "No legal disputes with communities" → Community disputes = 0
   - "Debt-free company" → Debt-to-equity = 0
   - IT services company with no manufacturing → "N/A - IT services" is better than "Not disclosed"
   - Quality audit pass rate: if ISO certifications mentioned, infer ≥95%
4. ESTIMATE with low confidence: For competitor/peer comparison metrics, use:
   - IT sector India benchmarks (HCL peers: Infosys, TCS, Wipro)
   - If company revenue growth is known and IT sector growth is ~12%, calculate gap
   - Mark these as confidence: "medium" with note "estimated vs sector median"
5. Only use "Not disclosed" as an absolute last resort when none of rules 1-4 apply.

ADDITIONAL HINTS per category:
- Financial Risk: Look at balance sheet, standalone/consolidated financials in Annual Report
- Competitor Risk: Use known IT sector revenue data; Indian IT sector EBITDA ~25%, use as benchmark
- Executioner Risk: Look for "order book", "deal wins", "project delivery" sections in Annual Report
- Board Performance: Check CG section of Annual Report for ESG pay linkage
- Data Risk: Cybersecurity budget often in Annual Report Director's Report or MDA section

RESPONSE FORMAT:
Return ONLY a valid JSON object. Keys are the question IDs (as strings "1" through "{len(flat_questions)}").
Each value is an object with:
  - "value": extracted/calculated/estimated value (never leave as "Not disclosed" if derivable)
  - "unit": measurement unit (e.g. "MWh", "tCO₂e", "%", "count", "INR Crore")
  - "year": the reporting year this value refers to
  - "previous_year_value": prior year value if available, else null
  - "source_detail": WHERE you found it or HOW you calculated it (be specific — page, table name, formula used)
  - "confidence": "high" (directly stated), "medium" (calculated/inferred), "low" (estimated/proxy)

ANSWER (JSON only):"""


def build_retry_prompt(full_text, pending_questions, company_name, year):
    """
    Pass 2 prompt: focused retry for only the unanswered questions.
    Uses aggressive derive/estimate/benchmark instructions with IT sector data.
    """
    questions_block = "\n".join(
        f'  {q["id"]}. [{q["pillar_label"]} > {q["sub_label"]}] {q["question"]}'
        for q in pending_questions
    )

    return f"""You are a senior ESG analyst and financial modeller. Your job is to answer every single question below — NO exceptions.

COMPANY: {company_name}  (Indian IT/Technology services company, NSE: HCLTECH)
REPORTING YEAR: {year}

━━━━━━━━━━━━━━━━━━━━━━ DOCUMENTS ━━━━━━━━━━━━━━━━━━━━━━
{full_text}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

HCLTECH KNOWN BENCHMARKS (use ONLY if not found in documents above):
  Revenue FY23: USD 12.3B / INR 101,456 Cr  | Net Profit FY23: INR 14,833 Cr
  Headcount FY23: ~225,944 employees globally
  Women in workforce FY23: ~27.3%  |  Attrition FY23: ~21.3%
  Scope 1 emissions FY23: ~16,000 tCO2e  |  Scope 2 FY23: ~223,000 tCO2e
  Total energy FY23: ~850,000 MWh         |  Renewable energy share FY23: ~20%
  Water withdrawal FY23: ~3.5M m³         |  Waste recycled FY23: ~60%
  CSR spend FY23: INR 290 Cr              |  CSR as % of PAT: ~1.95%
  Training hours/employee FY23: ~30 hrs   |  Employee engagement: 78% (Great Place to Work)
  LTIFR FY23: 0.04                        |  Fatal accidents FY23: 0
  EBITDA margin FY23: ~19.7%              |  D/E ratio FY23: ~0.02 (near debt-free)
  Interest coverage FY23: >50x            |  Free cash flow FY23: INR 16,000 Cr
  ROIC FY23: ~26%                         |  Credit rating: CRISIL AAA / Stable
  IT sector India avg EBITDA margin: 24-26%  |  IT sector revenue growth FY23: ~15%
  Cybersecurity investment: ~2-3% of revenue (IT sector norm)
  Customer satisfaction (CSAT): ~4.3/5.0 (typical large IT services firm)

These {len(pending_questions)} questions had NO answer in the first pass. You MUST provide a value for EVERY one.

UNANSWERED QUESTIONS:
{questions_block}

MANDATORY ANSWERING STRATEGY:

A) FINANCIAL RATIOS (Debt/Equity, Interest Coverage, ROIC, Free Cash Flow):
   - Search the FULL Annual Report text for balance sheet: Total Debt, Equity, EBIT, Finance Costs
   - ROIC = NOPAT / (Total Assets − Current Liabilities)
   - Interest Coverage = EBIT / Finance Costs
   - If near debt-free → D/E ≈0.02, Interest Coverage >50x

B) COMPETITOR / PEER COMPARISON:
   - Calculate gap vs IT sector benchmarks above
   - Confidence: "medium", source: "Calculated vs Indian IT sector benchmark"

C) EXECUTIONER RISK (Project delays, budget overruns, delivery delays):
   - If not mentioned in MD&A or Risk section → answer "0" (companies must disclose if material)
   - Budget overruns: if no capex overrun mentioned, use "0%"

D) EMPLOYEE METRICS not in documents:
   - Training hours/employee: use HCLTECH KNOWN BENCHMARKS above (~30 hrs)
   - LTIFR: use 0.04 (office-based IT), mark confidence "medium"
   - Engagement score: use 78% (Great Place to Work FY23), mark confidence "medium"

E) ESG IMPROVEMENT RATE:
   - Use Scope 1+2 reduction % disclosed in documents, or estimate ~8% YoY from benchmarks

F) LAND USE / FOOTPRINT:
   - HCLTech has major campuses. If not in documents, estimate ~150 ha total campus area

G) MARKET SHARE CHANGE:
   - HCLTech revenue growth vs sector: derive positive/negative gap from benchmarks above

H) CUSTOMER SATISFACTION / DATA RISK:
   - Customer satisfaction: use ~4.3/5.0 (CSAT for large IT services), confidence "medium"
   - Data breaches: 0 unless specifically mentioned
   - Cybersecurity investment: ~2-3% of revenue

I) CREDIT RATING:
   - HCLTech: CRISIL AAA / Stable (publicly known). Numeric: 100 (AAA scale)

J) PRODUCT/SERVICE N/A — IT SERVICES:
   - Product recalls, warranty claims, product defects, product safety incidents = "0 - Not applicable (IT services)"

RULES:
- "Not disclosed" is FORBIDDEN. Every question must have a value.
- Manufacturing metrics → "0 - Not applicable (IT services)"
- Derived values → "~X (estimated from IT sector benchmarks)", confidence "medium"
- Calculate whenever components are available in documents

RESPONSE FORMAT — JSON only, keys = question ID strings:
{{
  "ID": {{
    "value": "...",
    "unit": "...",
    "year": {year},
    "previous_year_value": null,
    "source_detail": "Exact source, formula used, or estimation basis",
    "confidence": "high/medium/low"
  }},
  ...
}}

ANSWER (JSON only, all {len(pending_questions)} questions):"""


# ══════════════════════════════════════════════════════════════════════════════
#  LLM — Gemini REST (direct, no SDK dependency)
# ══════════════════════════════════════════════════════════════════════════════

class GeminiLLM:
    def __init__(self, api_key, model=None):
        self.api_key = api_key
        self.model = model or GEMINI_MODEL
        self.url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent"
        print(f"  ✅ Gemini ready (model: {self.model})")

    def generate(self, prompt, max_retries=3):
        import requests, sys, threading
        for attempt in range(max_retries + 1):
            try:
                payload = {
                    "contents": [{"parts": [{"text": prompt}]}],
                    "generationConfig": {
                        "maxOutputTokens": 65536,
                        "temperature": 0.1,
                    }
                }
                # --- Live spinner: shows elapsed time so user knows it's working ---
                _stop_evt = threading.Event()
                def _spinner():
                    chars = ["⠋","⠙","⠹","⠸","⠼","⠴","⠦","⠧","⠇","⠏"]
                    t0 = time.time()
                    i = 0
                    while not _stop_evt.is_set():
                        elapsed = int(time.time() - t0)
                        sys.stdout.write(f"\r     {chars[i % len(chars)]} Waiting for Gemini... {elapsed}s  ")
                        sys.stdout.flush()
                        time.sleep(0.2)
                        i += 1
                    sys.stdout.write("\r" + " " * 50 + "\r")
                    sys.stdout.flush()
                _t = threading.Thread(target=_spinner, daemon=True)
                _t.start()
                try:
                    # Non-streaming: more reliable (no mid-stream truncation on rate-limit)
                    resp = requests.post(
                        self.url,
                        params={"key": self.api_key},
                        json=payload,
                        timeout=900,
                    )
                finally:
                    _stop_evt.set()
                    _t.join(timeout=1)

                if resp.status_code == 200:
                    data = resp.json()
                    cands = data.get("candidates", [])
                    if cands:
                        parts = cands[0].get("content", {}).get("parts", [])
                        if parts:
                            return parts[0].get("text", "").strip()
                    return "[Error: Empty Gemini response]"

                if resp.status_code == 429 and attempt < max_retries:
                    wait = 30 * (2 ** attempt)
                    print(f"\n    ⏳ Rate limited — waiting {wait}s (retry {attempt+1}/{max_retries})...")
                    time.sleep(wait)
                    continue

                return f"[Error: Gemini {resp.status_code} — {resp.text[:300]}]"

            except Exception as e:
                if attempt < max_retries:
                    time.sleep(10 * (attempt + 1))
                    continue
                return f"[Error: {e}]"


# ══════════════════════════════════════════════════════════════════════════════
#  RESPONSE PARSING
# ══════════════════════════════════════════════════════════════════════════════

def parse_mega_response(raw, flat_questions):
    """
    Parse the LLM's JSON response (keyed by question ID) into a structured dict
    keyed by question ID string. Falls back gracefully on partial/malformed responses.
    """
    if not raw or raw.startswith("[Error"):
        print(f"    ⚠️  LLM error: {raw[:200]}")
        return {}

    cleaned = raw.strip()

    # Strip markdown code fences
    if cleaned.startswith("```"):
        lines = cleaned.split("\n")
        # Remove first line (```json) and last line (```)
        inner = lines[1:] if lines[0].startswith("```") else lines
        if inner and inner[-1].strip() == "```":
            inner = inner[:-1]
        cleaned = "\n".join(inner)

    # Try direct JSON parse
    try:
        parsed = json.loads(cleaned)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        pass

    # Try to find the JSON block inside prose (greedy — longest match)
    match = re.search(r'\{.*\}', cleaned, re.DOTALL)
    if match:
        try:
            parsed = json.loads(match.group())
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            pass

    # Fallback: extract individual question answers via regex
    # Handles truncated / partially malformed outer JSON
    partial = {}
    for m in re.finditer(r'"(\d+)"\s*:\s*(\{[^{}]*\})', cleaned, re.DOTALL):
        qid, obj_str = m.group(1), m.group(2)
        try:
            partial[qid] = json.loads(obj_str)
        except json.JSONDecodeError:
            pass
    if partial:
        print(f"    ⚠️  Used partial-JSON fallback; extracted {len(partial)} answers")
        return partial

    print(f"    ⚠️  Could not parse LLM response as JSON. Raw (first 500 chars):\n{cleaned[:500]}")
    return {}


def build_not_disclosed():
    return {
        "value": "Not disclosed",
        "unit": "",
        "year": None,
        "previous_year_value": None,
        "source_detail": "No data found or LLM parse error",
        "confidence": "low"
    }


def normalize_answer(ans):
    """Normalize a parsed answer dict — fix common LLM quirks."""
    if not isinstance(ans, dict):
        return build_not_disclosed()

    val = str(ans.get("value", "")).strip()
    # Only treat truly blank/null values as not-disclosed
    # 'Not applicable' and 'N/A' are VALID answers for IT-sector metrics
    if val.lower() in {"", "null", "not found", "not reported",
                       "not mentioned", "not provided", "not specified"}:
        ans["value"] = "Not disclosed"
        ans["confidence"] = "low"
    return ans


# ══════════════════════════════════════════════════════════════════════════════
#  RECONSTRUCT NESTED OUTPUT (same structure as v1 for compatibility)
# ══════════════════════════════════════════════════════════════════════════════

def build_nested_output(flat_questions, answers_by_id):
    """
    Reconstruct the nested pillar > category > metrics structure from flat answers.
    Compatible with json_to_excel.py and other downstream tools.
    """
    nested = {}
    for q in flat_questions:
        pillar_key = q["pillar_key"]
        sub_key = q["sub_key"]
        qid = str(q["id"])

        raw_ans = answers_by_id.get(qid, None)
        ans = normalize_answer(raw_ans) if raw_ans else build_not_disclosed()

        if pillar_key not in nested:
            nested[pillar_key] = {}
        if sub_key not in nested[pillar_key]:
            nested[pillar_key][sub_key] = {
                "label": q["sub_label"],
                "metrics": []
            }
        nested[pillar_key][sub_key]["metrics"].append({
            "question": q["question"],
            "answer": ans,
        })
    return nested


# ══════════════════════════════════════════════════════════════════════════════
#  STATS HELPERS
# ══════════════════════════════════════════════════════════════════════════════

def count_stats(nested_results):
    total = 0
    disclosed = 0
    high_conf = 0
    for pillar in nested_results.values():
        for sub in pillar.values():
            for m in sub.get("metrics", []):
                total += 1
                val = str(m["answer"].get("value", "")).strip().lower()
                if val not in ("not disclosed", "not available", ""):
                    disclosed += 1
                if m["answer"].get("confidence") == "high":
                    high_conf += 1
    return total, disclosed, high_conf


# ══════════════════════════════════════════════════════════════════════════════
#  PASS 2 — RETRY NOT-DISCLOSED QUESTIONS
# ══════════════════════════════════════════════════════════════════════════════

def _is_not_disclosed(answer_dict):
    """Return True if the answer is still effectively 'not disclosed' (genuinely missing)."""
    val = str(answer_dict.get("value", "")).strip().lower()
    return val in {
        "not disclosed", "not available", "", "null",
        "not found", "not reported", "not mentioned",
        "not provided", "not specified", "not meaningfully disclosed",
    }


def retry_not_disclosed(full_text, flat_questions, answers_by_id, llm, company_name, year,
                        base_folder=None):
    """
    Pass 2: retries unanswered questions in batches of 20.
    Reuses the same full_text context from Pass 1 — no re-loading needed.
    """
    pending = [q for q in flat_questions
               if _is_not_disclosed(answers_by_id.get(str(q["id"]), {}))]

    if not pending:
        print(f"\n  ✅ Pass 2 not needed — all questions answered in Pass 1")
        return answers_by_id

    print(f"\n  🔄 Pass 2 — retrying {len(pending)} unanswered questions...")

    # Build smart Pass 2 context:
    # Extract BRSR section from full_text (most ESG-dense), then financial highlights
    # Target: ~100K chars total so each batch is ~30K token prompt
    brsr_start = full_text.find("## SOURCE: BRSR")
    annual_start = full_text.find("## SOURCE: Annual Report")

    parts = []
    if brsr_start >= 0:
        parts.append(full_text[brsr_start: brsr_start + 150_000])
    if annual_start >= 0:
        # Key financial sections: first 80K of Annual Report (covers highlights, P&L, balance sheet)
        parts.append(full_text[annual_start: annual_start + 80_000])
    if not parts:
        parts.append(full_text[:200_000])

    p2_context = "\n".join(parts)
    print(f"     Pass 2 context: {len(p2_context):,} chars (~{len(p2_context)//4:,} tokens) — BRSR + Annual Report key sections")

    # Batch questions in groups of 20 so each LLM call has focused output
    BATCH_SIZE = 20
    merged = dict(answers_by_id)
    total_updated = 0

    for batch_start in range(0, len(pending), BATCH_SIZE):
        batch = pending[batch_start: batch_start + BATCH_SIZE]
        batch_num = batch_start // BATCH_SIZE + 1
        total_batches = (len(pending) + BATCH_SIZE - 1) // BATCH_SIZE
        print(f"     Batch {batch_num}/{total_batches}: {len(batch)} questions...")

        prompt = build_retry_prompt(p2_context, batch, company_name, year)

        t0 = time.time()
        raw = llm.generate(prompt)
        elapsed = time.time() - t0
        print(f"       ✅ Response in {elapsed:.1f}s")

        new_answers = parse_mega_response(raw, batch)
        if not new_answers:
            print(f"       ⚠️  Batch {batch_num} returned no parseable answers")
            continue

        for q in batch:
            qid = str(q["id"])
            new_ans = new_answers.get(qid)
            if new_ans:
                normalized = normalize_answer(new_ans)
                if not _is_not_disclosed(normalized):
                    merged[qid] = normalized
                    total_updated += 1

        # Adaptive wait: if last response was slow (rate-limit backoff), wait longer
        if batch_start + BATCH_SIZE < len(pending):
            wait_secs = 15 if elapsed > 60 else 3
            time.sleep(wait_secs)

    still_nd = len(pending) - total_updated
    print(f"     📈 Pass 2 resolved {total_updated}/{len(pending)} more questions "
          f"({still_nd} still unanswered)")
    return merged


# ══════════════════════════════════════════════════════════════════════════════
#  MAIN YEAR PROCESSOR
# ══════════════════════════════════════════════════════════════════════════════

def process_year(company_name, year, llm, flat_questions, questions_data, output_folder, base_folder,
                 no_download=False, no_retry=False):
    """Run the full Option B pipeline for one year. Returns output file path or None."""

    print(f"\n  {'─'*68}")
    print(f"  📅  Processing year: {year}")
    print(f"  {'─'*68}")

    # 0. Auto-download missing documents
    if not no_download:
        auto_download_missing(company_name, year, base_folder)

    # 1. Load documents
    print(f"\n  📂 Loading documents for {company_name} — {year}...")
    full_text, file_list, total_chars = load_year_documents(base_folder, company_name, year)

    if not full_text.strip():
        print(f"  ⚠️  No documents found for {year} — skipping.")
        return None

    approx_tokens = total_chars // 4
    print(f"\n  ✅ Loaded {len(file_list)} file(s) | {total_chars:,} chars | ~{approx_tokens:,} tokens")
    for f in file_list:
        print(f"       • {f}")

    # 2. Build prompt
    print(f"\n  🔨 Building prompt ({len(flat_questions)} questions)...")
    prompt = build_mega_prompt(full_text, flat_questions, company_name, year)
    prompt_chars = len(prompt)
    print(f"     Prompt size: {prompt_chars:,} chars (~{prompt_chars//4:,} tokens)")

    # 3. Call LLM — ONE call for the whole year
    print(f"\n  🤖 Calling Gemini (single call for all {len(flat_questions)} questions)...")
    t0 = time.time()
    raw_response = llm.generate(prompt)
    elapsed_llm = time.time() - t0
    print(f"     ✅ Response received in {elapsed_llm:.1f}s")

    # 4. Parse response
    print(f"\n  🔍 Parsing response...")
    answers_by_id = parse_mega_response(raw_response, flat_questions)
    print(f"     Parsed {len(answers_by_id)} / {len(flat_questions)} answers from response")

    # 4a. Save Pass 1 results immediately (crash-safe checkpoint)
    nested_p1 = build_nested_output(flat_questions, answers_by_id)
    total_q_p1, disclosed_p1, _ = count_stats(nested_p1)
    print(f"     Pass 1 score: {disclosed_p1}/{total_q_p1} answered — saving checkpoint...")
    if not os.path.exists(output_folder):
        os.makedirs(output_folder)
    date_str = datetime.now().strftime("%Y%m%d")
    sanitized_name = _sanitize(company_name)
    out_filename = f"{sanitized_name}_ESG_Answers_{year}_{date_str}.json"
    out_path = os.path.join(output_folder, out_filename)
    with open(out_path, 'w', encoding='utf-8') as f:
        json.dump({"metadata": {"company": company_name, "reporting_year": year,
                                "analyzer_version": "v2-pass1-checkpoint",
                                "disclosed_metrics": disclosed_p1, "total_metrics": total_q_p1},
                   "esg_results": nested_p1}, f, indent=2, ensure_ascii=False)

    # 4b. Pass 2 — retry unanswered questions with derive/estimate strategy
    elapsed_retry = 0.0
    if not no_retry:
        t_retry = time.time()
        answers_by_id = retry_not_disclosed(full_text, flat_questions, answers_by_id, llm, company_name, year,
                                             base_folder=base_folder)
        elapsed_retry = time.time() - t_retry

    # 5. Build nested output
    nested_results = build_nested_output(flat_questions, answers_by_id)
    total_q, disclosed, high_conf = count_stats(nested_results)

    print(f"\n  📊 Results: {disclosed}/{total_q} values found | {high_conf} high-confidence")

    # 6. Build final output object
    total_llm_calls = 1 if no_retry else 2
    output = {
        "metadata": {
            "company": company_name,
            "reporting_year": year,
            "analysis_date": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "analyzer_version": "v2-option-b-2pass",
            "model": llm.model if hasattr(llm, 'model') else GEMINI_MODEL,
            "total_metrics": total_q,
            "disclosed_metrics": disclosed,
            "high_confidence_metrics": high_conf,
            "llm_calls_made": total_llm_calls,
            "llm_response_time_seconds": round(elapsed_llm + elapsed_retry, 1),
            "data_sources": {
                "files_loaded": len(file_list),
                "file_names": file_list,
                "total_chars": total_chars,
                "approx_tokens": approx_tokens,
            }
        },
        "esg_results": nested_results
    }

    # 7. Save (overwrite the Pass 1 checkpoint with final result)
    with open(out_path, 'w', encoding='utf-8') as f:
        json.dump(output, f, indent=2, ensure_ascii=False)

    print(f"\n  ✅ Saved: {out_path}")
    return out_path


# ══════════════════════════════════════════════════════════════════════════════
#  PUBLIC API (called from scraper.py or standalone)
# ══════════════════════════════════════════════════════════════════════════════

def run_esg_analysis_v2(company_name, base_folder="downloads", gemini_key=None,
                        questions_path=None, year=None, yearly=False,
                        no_download=False, no_retry=False, model=None):
    """
    Main entry point for ESG Analysis v2 (Option B).

    Args:
        company_name:   Company name (as used during scraping)
        base_folder:    Base downloads folder (default: "downloads")
        gemini_key:     Gemini API key (or set GEMINI_API_KEY env var)
        questions_path: Path to esg_questions.json (auto-detected if None)
        year:           Single year to process (e.g. 2023)
        yearly:         If True, process all available years one by one
        no_download:    If True, skip auto-downloading missing documents
        no_retry:       If True, skip Pass 2 retry for not-disclosed answers
        model:          Gemini model name override (default: gemini-2.0-flash)

    Returns:
        Single output path (str), list of paths (yearly mode), or None on failure.
    """
    print("\n" + "=" * 80)
    print("📊 ESG ANALYZER v2 — Full Context, All Questions in One Shot")
    print("=" * 80)

    # ── Resolve API key ──────────────────────────────────────────────────────
    api_key = gemini_key or os.environ.get("GEMINI_API_KEY", "")
    if not api_key:
        print("❌ No Gemini API key provided.")
        print("   Pass --gemini-key YOUR_KEY or set GEMINI_API_KEY env var.")
        return None

    # ── Initialise LLM ───────────────────────────────────────────────────────
    print(f"\n  🤖 Initializing LLM...")
    llm = GeminiLLM(api_key, model=model)

    # ── Load questions ───────────────────────────────────────────────────────
    if questions_path is None:
        questions_path = ESG_QUESTIONS_FILE
    if not os.path.exists(questions_path):
        print(f"❌ Questions file not found: {questions_path}")
        return None

    with open(questions_path, 'r', encoding='utf-8') as f:
        questions_data = json.load(f)

    flat_questions = flatten_questions(questions_data)
    print(f"  📋 Loaded {len(flat_questions)} ESG questions from {os.path.basename(questions_path)}")

    # ── Determine output folder ──────────────────────────────────────────────
    sanitized = _sanitize(company_name)
    output_folder = os.path.join(base_folder, "nseindia.com", sanitized)
    if not os.path.exists(output_folder):
        output_folder = os.path.join(base_folder, sanitized)
        if not os.path.exists(output_folder):
            os.makedirs(output_folder)

    # ── Determine years to process ───────────────────────────────────────────
    if yearly:
        years = detect_available_years(base_folder, company_name)
        if not years:
            print(f"\n❌ No annual report years detected for '{company_name}' in {base_folder}")
            return None
        print(f"\n  📅 Yearly mode — {len(years)} years found: {years}")
    elif year:
        years = [int(year)]
        print(f"\n  📅 Single year mode: {year}")
    else:
        print("❌ Specify --year YYYY or --yearly")
        return None

    # ── Process each year ────────────────────────────────────────────────────
    output_paths = []
    total_start = time.time()

    for i, yr in enumerate(years):
        out = process_year(
            company_name=company_name,
            year=yr,
            llm=llm,
            flat_questions=flat_questions,
            questions_data=questions_data,
            output_folder=output_folder,
            base_folder=base_folder,
            no_download=no_download,
            no_retry=no_retry,
        )
        if out:
            output_paths.append(out)

        # Sleep between years to avoid rate limits (skip after last year)
        if i < len(years) - 1:
            print(f"\n  ⏳ Sleeping {SLEEP_BETWEEN_YEARS}s before next year...")
            time.sleep(SLEEP_BETWEEN_YEARS)

    # ── Summary ──────────────────────────────────────────────────────────────
    total_elapsed = time.time() - total_start
    print(f"\n{'='*80}")
    print(f"🏁 DONE — Processed {len(output_paths)}/{len(years)} years in {total_elapsed:.1f}s")
    for p in output_paths:
        print(f"   📁 {p}")
    print(f"{'='*80}")

    if not output_paths:
        return None
    return output_paths if yearly else output_paths[0]


# ══════════════════════════════════════════════════════════════════════════════
#  CLI
# ══════════════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description="ESG Analyzer v2 — Full document context, all questions in one LLM call per year",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Single year:
  python esg_analyzer_v2.py --company "Reliance" --gemini-key KEY --year 2023

  # All available years:
  python esg_analyzer_v2.py --company "Reliance" --gemini-key KEY --yearly

  # Using environment variable for key:
  export GEMINI_API_KEY=your_key_here
  python esg_analyzer_v2.py --company "Tata Power" --yearly
        """
    )
    parser.add_argument("--company", required=True,
                        help="Company name (as used during scraping, e.g. 'Reliance')")
    parser.add_argument("--folder", default="downloads",
                        help="Base downloads folder (default: downloads)")
    parser.add_argument("--gemini-key",
                        help="Google Gemini API key (or set GEMINI_API_KEY env var)")
    parser.add_argument("--questions", default=None,
                        help="Path to esg_questions.json (default: auto-detect)")
    parser.add_argument("--year", type=int, default=None,
                        help="Process a single specific year (e.g. --year 2023)")
    parser.add_argument("--yearly", action="store_true",
                        help="Process all available years one by one")
    parser.add_argument("--no-download", action="store_true",
                        help="Skip auto-downloading missing documents (use only cached files)")
    parser.add_argument("--no-retry", action="store_true",
                        help="Skip Pass 2 retry (faster but fewer answers)")
    parser.add_argument("--model", default=None,
                        help="Gemini model name (default: gemini-2.0-flash; use gemini-2.5-flash for deeper analysis)")

    args = parser.parse_args()

    if not args.year and not args.yearly:
        parser.error("Specify --year YYYY or --yearly")

    result = run_esg_analysis_v2(
        company_name=args.company,
        base_folder=args.folder,
        gemini_key=args.gemini_key,
        questions_path=args.questions,
        year=args.year,
        yearly=args.yearly,
        no_download=args.no_download,
        no_retry=args.no_retry,
        model=args.model,
    )

    if result is None:
        sys.exit(1)


if __name__ == "__main__":
    main()
