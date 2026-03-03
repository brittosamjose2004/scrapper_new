"""
ESG Question Answering Module

Reads all scraped data (PDFs, News JSON, Social Media JSON) and uses an LLM
(Google Gemini API or Ollama) to answer ESG questions from esg_questions.json.
Saves results as structured JSON.

Usage:
  # Standalone (after scraping):
  python esg_analyzer.py --company "Reliance" --folder downloads --llm gemini

  # With explicit API key:
  python esg_analyzer.py --company "Reliance" --llm gemini --gemini-key YOUR_KEY
"""

import os
import sys
import json
import time
import argparse
import logging
from datetime import datetime

# Fix Windows console encoding for emoji output
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding='utf-8')
        sys.stderr.reconfigure(encoding='utf-8')
    except Exception:
        pass

# PDF extraction
try:
    from pypdf import PdfReader
except ImportError:
    PdfReader = None

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# ─── Constants ─────────────────────────────────────────────────────────────────
ESG_QUESTIONS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "esg_questions.json")
MAX_CONTEXT_CHARS = 25000
TOP_K_CHUNKS = 5
GEMINI_MODEL = "gemini-2.0-flash"
OLLAMA_MODEL = "gemma:7b"
OLLAMA_URL = "http://localhost:11434"


# ═══════════════════════════════════════════════════════════════════════════════
#  LLM BACKENDS
# ═══════════════════════════════════════════════════════════════════════════════

class GeminiLLM:
    """Google Gemini API backend (free tier)."""

    def __init__(self, api_key=None):
        try:
            import google.generativeai as genai
        except ImportError:
            print("❌ google-generativeai not installed. Run: pip install google-generativeai")
            sys.exit(1)

        self.api_key = api_key or os.environ.get("GEMINI_API_KEY")
        if not self.api_key:
            print("❌ GEMINI_API_KEY not set.")
            print("   Get a free key at: https://aistudio.google.com/apikey")
            print("   Then: set GEMINI_API_KEY=your_key_here")
            sys.exit(1)

        genai.configure(api_key=self.api_key)
        self.model = genai.GenerativeModel(GEMINI_MODEL)
        print(f"  ✅ Gemini LLM ready (model: {GEMINI_MODEL})")

    def generate(self, prompt, max_retries=3):
        for attempt in range(max_retries + 1):
            try:
                response = self.model.generate_content(prompt)
                return response.text.strip()
            except Exception as e:
                error_str = str(e)
                # Handle rate limiting with retry
                if "429" in error_str and attempt < max_retries:
                    wait = 15 * (2 ** attempt)  # 15s, 30s, 60s
                    print(f"\n      ⏳ Rate limited, waiting {wait}s (retry {attempt+1}/{max_retries})...", end=" ", flush=True)
                    time.sleep(wait)
                    continue
                logger.warning(f"Gemini API error: {e}")
                return f"[Error: {e}]"


class OllamaLLM:
    """Local Ollama backend."""

    def __init__(self, model=OLLAMA_MODEL, base_url=OLLAMA_URL):
        import requests as req
        self.model = model
        self.base_url = base_url
        self.url = f"{base_url}/api/generate"
        try:
            req.get(base_url, timeout=5)
            print(f"  ✅ Ollama LLM ready (model: {model})")
        except Exception:
            print(f"❌ Cannot reach Ollama at {base_url}. Is 'ollama serve' running?")
            sys.exit(1)

    def generate(self, prompt):
        import requests as req
        try:
            resp = req.post(self.url, json={
                "model": self.model,
                "prompt": prompt,
                "stream": False
            }, timeout=120)
            if resp.status_code == 200:
                return resp.json().get("response", "").strip()
            return f"[Ollama Error: {resp.status_code}]"
        except Exception as e:
            return f"[Error: {e}]"


# ═══════════════════════════════════════════════════════════════════════════════
#  DATA LOADING — Read all scraped PDFs and JSONs
# ═══════════════════════════════════════════════════════════════════════════════

def _sanitize(name):
    """Sanitize company name same way as scraper.py."""
    return "".join([c for c in name if c.isalpha() or c.isdigit() or c in (' ', '-', '_')]).strip()


def extract_pdf_text(pdf_path):
    """Extract text from a PDF → list of chunk dicts."""
    if PdfReader is None:
        logger.warning("pypdf not installed — skipping PDFs. Run: pip install pypdf")
        return []
    try:
        reader = PdfReader(pdf_path)
        pages = []
        for i, page in enumerate(reader.pages):
            text = page.extract_text()
            if text and len(text.strip()) > 50:
                pages.append({
                    "text": text,
                    "source": f"{os.path.basename(pdf_path)}, Page {i+1}",
                    "type": "pdf"
                })
        return pages
    except Exception as e:
        logger.warning(f"  ⚠️  Failed to read PDF {os.path.basename(pdf_path)}: {e}")
        return []


def load_json_data(json_path):
    """Load a news/social JSON file → list of chunk dicts."""
    chunks = []
    try:
        with open(json_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        if not isinstance(data, list):
            data = [data]

        for i, item in enumerate(data):
            text = ""
            if isinstance(item, dict):
                title = item.get("title", "")
                content = item.get("content", "")
                text = f"{title}\n{content}" if title else content
            elif isinstance(item, str):
                text = item

            if text and len(text.strip()) > 20:
                src_type = "news" if "news" in json_path.lower() else "social"
                chunks.append({
                    "text": text,
                    "source": f"{os.path.basename(json_path)}, Item {i+1}",
                    "type": src_type
                })
        return chunks
    except Exception as e:
        logger.warning(f"  ⚠️  Failed to load JSON {os.path.basename(json_path)}: {e}")
        return []


def load_all_data(base_folder, company_name):
    """
    Load ALL scraped data for a company from every possible folder structure.
    Returns (chunks_list, sources_summary_dict).
    """
    all_chunks = []
    summary = {"pdf_count": 0, "pdf_pages": 0, "news_items": 0, "social_items": 0}
    sanitized = _sanitize(company_name)
    name_lower = sanitized.lower()

    # Build list of candidate directories to scan
    search_dirs = set()

    # Pattern 1: downloads/<company_name>/
    search_dirs.add(os.path.join(base_folder, sanitized))

    # Pattern 2: downloads/annualreports.com/<company_name>/
    search_dirs.add(os.path.join(base_folder, "annualreports.com", sanitized))

    # Pattern 3: downloads/nseindia.com/<company_name>/
    search_dirs.add(os.path.join(base_folder, "nseindia.com", sanitized))

    # Pattern 4: Fuzzy match — scan base_folder for any folder whose name
    #             contains the company name (case-insensitive)
    if os.path.exists(base_folder):
        for entry in os.listdir(base_folder):
            entry_path = os.path.join(base_folder, entry)
            if os.path.isdir(entry_path):
                if entry.lower() == name_lower or name_lower in entry.lower():
                    search_dirs.add(entry_path)
                # Also check inside annualreports.com / nseindia.com subdirs
                if entry in ("annualreports.com", "nseindia.com"):
                    for sub in os.listdir(entry_path):
                        sub_path = os.path.join(entry_path, sub)
                        if os.path.isdir(sub_path) and name_lower in sub.lower():
                            search_dirs.add(sub_path)

    # Deduplicate and filter to existing dirs
    search_dirs = sorted([d for d in search_dirs if os.path.exists(d)])

    if not search_dirs:
        return all_chunks, summary

    print(f"    Scanning {len(search_dirs)} folder(s):")
    for d in search_dirs:
        print(f"      → {d}")

    for search_dir in search_dirs:
        # First pass: collect all filenames to know which PDFs have TXT versions
        all_files_in_dir = {}
        for root, _, files in os.walk(search_dir):
            for fname in files:
                all_files_in_dir[os.path.join(root, fname)] = fname

        txt_basenames = set()
        for fpath, fname in all_files_in_dir.items():
            if fname.lower().endswith('.txt'):
                txt_basenames.add(os.path.splitext(fname)[0].lower())

        for filepath, fname in sorted(all_files_in_dir.items()):
            fname_lower = fname.lower()

            # Load TXT files (pre-extracted from PDFs)
            if fname_lower.endswith('.txt'):
                print(f"    📝 {fname}")
                try:
                    with open(filepath, 'r', encoding='utf-8') as f:
                        content = f.read()
                    # Split by page markers if present
                    if "--- PAGE " in content:
                        pages = content.split("--- PAGE ")
                        for page in pages[1:]:  # skip header
                            lines = page.split("\n", 1)
                            page_num = lines[0].strip().replace(" ---", "")
                            text = lines[1] if len(lines) > 1 else ""
                            if text and len(text.strip()) > 50:
                                all_chunks.append({
                                    "text": text,
                                    "source": f"{fname}, Page {page_num}",
                                    "type": "pdf"
                                })
                                summary["pdf_pages"] += 1
                    else:
                        # No page markers — treat entire file as one chunk
                        if content and len(content.strip()) > 50:
                            all_chunks.append({
                                "text": content,
                                "source": fname,
                                "type": "pdf"
                            })
                            summary["pdf_pages"] += 1
                    summary["pdf_count"] += 1
                except Exception as e:
                    logger.warning(f"    Failed to read TXT {fname}: {e}")

            # Load PDFs ONLY if no TXT version exists
            elif fname_lower.endswith('.pdf'):
                base = os.path.splitext(fname)[0].lower()
                if base in txt_basenames:
                    print(f"    ⏭️  {fname} (using TXT version)")
                    continue
                print(f"    📄 {fname}")
                chunks = extract_pdf_text(filepath)
                all_chunks.extend(chunks)
                summary["pdf_count"] += 1
                summary["pdf_pages"] += len(chunks)

            elif fname_lower.endswith('.json') and "ESG_Answers" not in fname:
                print(f"    📊 {fname}")
                chunks = load_json_data(filepath)
                all_chunks.extend(chunks)
                if "news" in fname_lower:
                    summary["news_items"] += len(chunks)
                else:
                    summary["social_items"] += len(chunks)

    return all_chunks, summary


# ═══════════════════════════════════════════════════════════════════════════════
#  CONTEXT RETRIEVAL — keyword-based relevance scoring
# ═══════════════════════════════════════════════════════════════════════════════

def find_relevant_chunks(question, all_chunks, top_k=TOP_K_CHUNKS):
    """Find the most relevant text chunks for a question using keyword matching."""
    # Build keyword list from the question
    keywords = [w.lower() for w in question.replace('/', ' ').replace('(', ' ').replace(')', ' ').split() if len(w) > 3]

    # Add domain-specific synonyms
    synonym_map = {
        "energy": ["electricity", "power", "kwh", "mwh", "fuel", "diesel", "coal"],
        "water": ["water", "effluent", "discharge", "withdrawal"],
        "emissions": ["emission", "co2", "carbon", "ghg", "greenhouse", "scope"],
        "waste": ["waste", "hazardous", "recycl", "disposal", "landfill"],
        "employee": ["employee", "worker", "staff", "workforce", "manpower", "personnel"],
        "safety": ["safety", "accident", "injury", "fatality", "ltifr", "incident"],
        "csr": ["csr", "community", "social", "philanthropy", "donation"],
        "board": ["board", "director", "independent", "governance", "chairman"],
        "compliance": ["compliance", "violation", "penalty", "fine", "regulatory", "legal"],
        "diversity": ["diversity", "women", "female", "gender", "minority"],
        "revenue": ["revenue", "turnover", "income", "sales", "profit"],
        "debt": ["debt", "borrowing", "loan", "leverage", "equity"],
        "capex": ["capex", "capital", "investment", "expenditure"],
        "cybersecurity": ["cyber", "data", "breach", "privacy", "security"],
        "customer": ["customer", "consumer", "complaint", "satisfaction", "recall"],
    }

    expanded_keywords = set(keywords)
    for kw in keywords:
        for key, synonyms in synonym_map.items():
            if kw in synonyms or key in kw:
                expanded_keywords.update(synonyms)

    scored = []
    for chunk in all_chunks:
        text_lower = chunk["text"].lower()
        score = 0
        for kw in expanded_keywords:
            score += text_lower.count(kw)
        if score > 0:
            scored.append((score, chunk))

    scored.sort(key=lambda x: x[0], reverse=True)
    results = [s[1] for s in scored[:top_k]]

    # Fallback: if no match, grab first couple of chunks
    if not results and all_chunks:
        results = all_chunks[:min(2, len(all_chunks))]

    return results


def build_context(relevant_chunks, max_chars=MAX_CONTEXT_CHARS):
    """Combine chunks into a single context string."""
    parts = []
    total = 0
    for chunk in relevant_chunks:
        entry = f"[Source: {chunk['source']}]\n{chunk['text']}"
        if total + len(entry) > max_chars:
            remaining = max_chars - total
            if remaining > 200:
                parts.append(entry[:remaining] + "\n...[truncated]")
            break
        parts.append(entry)
        total += len(entry)
    return "\n\n---\n\n".join(parts)


# ═══════════════════════════════════════════════════════════════════════════════
#  PROMPT BUILDING
# ═══════════════════════════════════════════════════════════════════════════════

def build_prompt(question, category, subcategory, context, company_name):
    """Build an LLM prompt for answering one ESG metric question."""
    return f"""You are an expert ESG data analyst extracting specific metrics for an Indian listed company: {company_name}.

CONTEXT (from Annual Reports, Sustainability Reports, BRSR filings, News articles):
{context}

ESG CATEGORY: {category} > {subcategory}
METRIC TO EXTRACT: {question}

INSTRUCTIONS:
1. Extract the EXACT numeric value or data for this metric from the context above.
2. If the metric is a percentage, provide the % value. If it's a count, provide the number.
3. If data is available for multiple years, provide the most recent value AND previous year for comparison.
4. Format your answer as a JSON object with these fields:
   - "value": the extracted value (number, string, or "Not disclosed")
   - "unit": the unit of measurement (e.g., "MWh", "%", "tCO₂e", "count", "$")
   - "year": the reporting year if identifiable
   - "previous_year_value": previous year value if available, else null
   - "source_detail": brief note on where in the document this was found
   - "confidence": "high", "medium", or "low" based on how clearly the data was stated
5. If the data is NOT found, set value to "Not disclosed" and confidence to "low".
6. Return ONLY the JSON object, no other text.

ANSWER:"""


# ═══════════════════════════════════════════════════════════════════════════════
#  ESG ANALYZER — Main class
# ═══════════════════════════════════════════════════════════════════════════════

class ESGAnalyzer:
    """Processes all ESG questions against scraped data using an LLM."""

    def __init__(self, llm, all_chunks, company_name, sources_summary):
        self.llm = llm
        self.all_chunks = all_chunks
        self.company_name = company_name
        self.sources_summary = sources_summary
        self.total_questions = 0
        self.answered = 0

    def _parse_llm_answer(self, raw_answer):
        """Try to parse LLM response as JSON. Fallback to raw string."""
        # Strip markdown code fences if present
        cleaned = raw_answer.strip()
        if cleaned.startswith("```"):
            # Remove first and last lines
            lines = cleaned.split("\n")
            cleaned = "\n".join(lines[1:-1]) if len(lines) > 2 else cleaned

        try:
            return json.loads(cleaned)
        except json.JSONDecodeError:
            # Couldn't parse — return as raw answer
            return {
                "value": raw_answer.strip(),
                "unit": "",
                "year": None,
                "previous_year_value": None,
                "source_detail": "",
                "confidence": "low"
            }

    def process_questions(self, questions_data):
        """Walk through the questions JSON and answer each one."""
        results = {}

        for pillar_key, pillar_data in questions_data.items():
            # e.g. pillar_key = "environmental_performance"
            pillar_label = pillar_key.replace("_", " ").title()
            print(f"\n  {'─'*60}")
            print(f"  📌 {pillar_label}")
            print(f"  {'─'*60}")

            results[pillar_key] = {}

            for sub_key, sub_data in pillar_data.items():
                # e.g. sub_key = "resource_use"
                sub_label = sub_key.replace("_", " ").title()
                questions = sub_data.get("questions", [])
                print(f"\n    📂 {sub_label} ({len(questions)} metrics)")

                answered_list = []

                for q in questions:
                    self.answered += 1
                    progress = f"[{self.answered}/{self.total_questions}]"

                    display_q = q[:55] + "..." if len(q) > 55 else q
                    print(f"      {progress} {display_q}", end=" ", flush=True)

                    # Find relevant context
                    relevant = find_relevant_chunks(q, self.all_chunks)
                    context = build_context(relevant)

                    if not context.strip():
                        print("→ No data")
                        answered_list.append({
                            "question": q,
                            "answer": {
                                "value": "Not disclosed",
                                "unit": "",
                                "year": None,
                                "previous_year_value": None,
                                "source_detail": "No relevant data found in scraped sources",
                                "confidence": "low"
                            },
                            "sources_searched": 0
                        })
                        continue

                    # Call LLM
                    prompt = build_prompt(q, pillar_label, sub_label, context, self.company_name)
                    raw = self.llm.generate(prompt)
                    parsed = self._parse_llm_answer(raw)

                    conf = parsed.get("confidence", "low")
                    val = parsed.get("value", "?")
                    display_val = str(val)[:30]
                    print(f"→ {display_val} ({conf})")

                    answered_list.append({
                        "question": q,
                        "answer": parsed,
                        "sources_searched": len(relevant)
                    })

                    # Rate limit pause (Gemini free tier: ~15 RPM)
                    time.sleep(4.0)

                results[pillar_key][sub_key] = {
                    "label": sub_label,
                    "metrics": answered_list
                }

        return results

    def run(self, questions_path, output_folder):
        """Run the full ESG analysis pipeline."""
        # Load questions
        print(f"\n  📋 Loading ESG questions from {os.path.basename(questions_path)}...")
        with open(questions_path, 'r', encoding='utf-8') as f:
            questions_data = json.load(f)

        # Count total questions
        for pillar in questions_data.values():
            for sub in pillar.values():
                self.total_questions += len(sub.get("questions", []))

        print(f"     Total metrics to extract: {self.total_questions}")
        print(f"     Data chunks available: {len(self.all_chunks)}")

        # Process
        print(f"\n  🤖 Starting LLM extraction...\n")
        start_time = time.time()

        filled_results = self.process_questions(questions_data)

        elapsed = time.time() - start_time

        # Build final output
        output = {
            "metadata": {
                "company": self.company_name,
                "analysis_date": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "total_metrics": self.total_questions,
                "metrics_processed": self.answered,
                "time_taken_seconds": round(elapsed, 1),
                "llm_provider": type(self.llm).__name__,
                "data_sources": {
                    "pdf_files": self.sources_summary.get("pdf_count", 0),
                    "pdf_pages_extracted": self.sources_summary.get("pdf_pages", 0),
                    "news_articles": self.sources_summary.get("news_items", 0),
                    "social_media_posts": self.sources_summary.get("social_items", 0),
                    "total_text_chunks": len(self.all_chunks)
                }
            },
            "esg_results": filled_results
        }

        # Save
        if not os.path.exists(output_folder):
            os.makedirs(output_folder)

        date_str = datetime.now().strftime("%Y%m%d")
        sanitized = _sanitize(self.company_name)
        out_filename = f"{sanitized}_ESG_Answers_{date_str}.json"
        out_path = os.path.join(output_folder, out_filename)

        with open(out_path, 'w', encoding='utf-8') as f:
            json.dump(output, f, indent=2, ensure_ascii=False)

        print(f"\n  {'='*60}")
        print(f"  ✅ ESG Analysis Complete!")
        print(f"     Metrics extracted: {self.answered}/{self.total_questions}")
        print(f"     Time taken: {elapsed:.1f}s ({elapsed/60:.1f} min)")
        print(f"     Output saved: {out_path}")
        print(f"  {'='*60}")

        return out_path


# ═══════════════════════════════════════════════════════════════════════════════
#  PUBLIC API — called from scraper.py or standalone
# ═══════════════════════════════════════════════════════════════════════════════

def run_esg_analysis(company_name, base_folder="downloads", llm_type="gemini",
                     gemini_key=None, questions_path=None):
    """
    Main entry point for ESG analysis.

    Args:
        company_name: Company name (as used during scraping)
        base_folder:  Base downloads folder
        llm_type:     "gemini" or "ollama"
        gemini_key:   Gemini API key (or set GEMINI_API_KEY env var)
        questions_path: Path to esg_questions.json (auto-detected if None)

    Returns:
        Path to output JSON file, or None on failure.
    """
    print("\n" + "=" * 80)
    print("📊 ESG ANALYSIS — Extracting Metrics from Scraped Data")
    print("=" * 80)

    # 1. Load data
    print(f"\n  📂 Loading all scraped data for '{company_name}'...")
    all_chunks, summary = load_all_data(base_folder, company_name)

    if not all_chunks:
        print("\n  ❌ No data found! Make sure you have scraped data first.")
        print(f"     Expected data in: {base_folder}/*/")
        return None

    print(f"\n  ✅ Loaded {len(all_chunks)} text chunks from {summary['pdf_count']} PDFs, "
          f"{summary['news_items']} news, {summary['social_items']} social posts")

    # 2. Init LLM
    print(f"\n  🤖 Initializing {llm_type.upper()} LLM...")
    if llm_type == "gemini":
        llm = GeminiLLM(api_key=gemini_key)
    elif llm_type == "ollama":
        llm = OllamaLLM()
    else:
        print(f"  ❌ Unknown LLM type: {llm_type}. Use 'gemini' or 'ollama'.")
        return None

    # 3. Resolve questions file
    if questions_path is None:
        questions_path = ESG_QUESTIONS_FILE
    if not os.path.exists(questions_path):
        print(f"  ❌ Questions file not found: {questions_path}")
        return None

    # 4. Determine output folder
    sanitized = _sanitize(company_name)
    output_folder = os.path.join(base_folder, "nseindia.com", sanitized)
    if not os.path.exists(output_folder):
        output_folder = os.path.join(base_folder, sanitized)

    # 5. Run
    analyzer = ESGAnalyzer(llm, all_chunks, company_name, summary)
    return analyzer.run(questions_path, output_folder)


# ═══════════════════════════════════════════════════════════════════════════════
#  CLI
# ═══════════════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="ESG Metric Extraction from Scraped Data")
    parser.add_argument("--company", required=True, help="Company name (as used during scraping)")
    parser.add_argument("--folder", default="downloads", help="Base downloads folder (default: downloads)")
    parser.add_argument("--llm", choices=["gemini", "ollama"], default="gemini",
                        help="LLM backend (default: gemini)")
    parser.add_argument("--gemini-key", help="Gemini API key (or set GEMINI_API_KEY env var)")
    parser.add_argument("--questions", default=None, help="Path to ESG questions JSON (default: esg_questions.json)")

    args = parser.parse_args()

    output = run_esg_analysis(
        company_name=args.company,
        base_folder=args.folder,
        llm_type=args.llm,
        gemini_key=args.gemini_key,
        questions_path=args.questions
    )

    if output:
        print(f"\n🎉 Done! Results saved to: {output}")
    else:
        print("\n❌ Analysis failed.")
        sys.exit(1)


if __name__ == "__main__":
    main()
