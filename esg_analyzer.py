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
import re
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
MAX_CONTEXT_CHARS = 40000
TOP_K_CHUNKS = int(os.environ.get("TOP_K_CHUNKS", "20"))   # env-configurable, default 20
GEMINI_MODEL = "gemini-2.5-flash"
OLLAMA_MODEL = "gemma:7b"
OLLAMA_URL = "http://localhost:11434"
GROK_MODEL = "grok-3-mini"
# Default OpenRouter model — override via OPENROUTER_MODEL env var if needed
OPENROUTER_MODEL = os.environ.get("OPENROUTER_MODEL", "google/gemma-3-12b-it:free")


def _split_keys(value):
    if not value:
        return []
    return [v.strip() for v in value.split(",") if v.strip()]


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


class GeminiRestLLM:
    """Gemini REST backend with explicit API key (for key rotation/fallback)."""

    def __init__(self, api_key, model=GEMINI_MODEL):
        self.api_key = api_key
        self.model = model
        self.url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

    def generate(self, prompt, max_retries=2):
        import requests as req
        for attempt in range(max_retries + 1):
            try:
                resp = req.post(
                    self.url,
                    params={"key": self.api_key},
                    json={
                        "contents": [{"parts": [{"text": prompt}]}]
                    },
                    timeout=120,
                )
                if resp.status_code == 200:
                    data = resp.json()
                    cands = data.get("candidates", [])
                    if cands:
                        parts = cands[0].get("content", {}).get("parts", [])
                        if parts:
                            return parts[0].get("text", "").strip()
                    return "[Error: Empty Gemini response]"

                if resp.status_code == 429 and attempt < max_retries:
                    wait = 10 * (2 ** attempt)
                    print(f"\n      ⏳ Gemini REST rate-limited, waiting {wait}s...", end=" ", flush=True)
                    time.sleep(wait)
                    continue

                return f"[Error: Gemini REST {resp.status_code} {resp.text[:300]}]"
            except Exception as e:
                if attempt < max_retries:
                    time.sleep(2 * (attempt + 1))
                    continue
                return f"[Error: {e}]"


class OpenAICompatLLM:
    """OpenAI-compatible chat-completions backend (xAI/OpenRouter)."""

    def __init__(self, api_key, base_url, model, provider_name):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.provider_name = provider_name
        self.url = f"{self.base_url}/chat/completions"
        self.max_tokens = int(os.environ.get("LLM_MAX_TOKENS", "512"))

    def generate(self, prompt, max_retries=2):
        import requests as req
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        for attempt in range(max_retries + 1):
            try:
                resp = req.post(
                    self.url,
                    headers=headers,
                    json={
                        "model": self.model,
                        "messages": [
                            {"role": "system", "content": "You are a precise ESG extraction assistant."},
                            {"role": "user", "content": prompt},
                        ],
                        "temperature": 0.1,
                        "max_tokens": self.max_tokens,
                    },
                    timeout=120,
                )
                if resp.status_code == 200:
                    data = resp.json()
                    choices = data.get("choices", [])
                    if choices:
                        msg = choices[0].get("message", {})
                        return (msg.get("content") or "").strip()
                    return f"[Error: Empty {self.provider_name} response]"

                if resp.status_code in (408, 429, 500, 502, 503, 504) and attempt < max_retries:
                    wait = 8 * (2 ** attempt)
                    print(f"\n      ⏳ {self.provider_name} temporary error ({resp.status_code}), waiting {wait}s...", end=" ", flush=True)
                    time.sleep(wait)
                    continue

                return f"[Error: {self.provider_name} {resp.status_code} {resp.text[:300]}]"
            except Exception as e:
                if attempt < max_retries:
                    time.sleep(2 * (attempt + 1))
                    continue
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


class MultiProviderLLM:
    """Try multiple providers/keys in sequence when one fails/quota-exceeds."""

    def __init__(self, clients):
        self.clients = clients
        self.last_provider = ""
        self.disabled_providers = set()

    def _is_failure(self, text):
        if not isinstance(text, str):
            return False
        low = text.lower()
        return low.startswith("[error")

    def _is_permanent_provider_failure(self, text):
        if not isinstance(text, str):
            return False
        low = text.lower()
        permanent_markers = [
            " 401 ",
            " 402 ",
            "insufficient credits",
            "insufficient_quota",
            "billing",
            "payment",
            "invalid api key",
            "unauthorized",
            "forbidden",
        ]
        return any(marker in low for marker in permanent_markers)

    def generate(self, prompt):
        last_error = "[Error: No providers configured]"
        for name, client in self.clients:
            if name in self.disabled_providers:
                continue
            self.last_provider = name
            out = client.generate(prompt)
            if not self._is_failure(out):
                return out
            if self._is_permanent_provider_failure(out):
                self.disabled_providers.add(name)
                print(f"\n      ↪ Disabling provider {name} (permanent error)", end=" ", flush=True)
            print(f"\n      ↪ Switching provider after failure on {name}", end=" ", flush=True)
            last_error = out
        return last_error


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


def detect_available_years(base_folder, company_name):
    """Scan company folder and return sorted list of years that have annual report files."""
    sanitized = _sanitize(company_name)
    name_lower = sanitized.lower()

    candidate_dirs = [
        os.path.join(base_folder, "nseindia.com", sanitized),
        os.path.join(base_folder, sanitized),
    ]
    if os.path.exists(base_folder):
        for entry in os.listdir(base_folder):
            entry_path = os.path.join(base_folder, entry)
            if entry in ("annualreports.com", "nseindia.com") and os.path.isdir(entry_path):
                for sub in os.listdir(entry_path):
                    sub_path = os.path.join(entry_path, sub)
                    if os.path.isdir(sub_path) and name_lower in sub.lower():
                        candidate_dirs.append(sub_path)

    years = set()
    for d in candidate_dirs:
        if not os.path.exists(d):
            continue
        for fname in os.listdir(d):
            m = re.match(r'^(\d{4})_', fname)
            if m:
                yr = int(m.group(1))
                if 2000 <= yr <= 2030:
                    years.add(yr)
    return sorted(years)


def load_all_data(base_folder, company_name, year=None):
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

            # ── Year filter: skip files that don't belong to the requested year ──
            if year is not None:
                year_str = str(year)
                rel = os.path.relpath(filepath, search_dir)
                rel_parts = rel.split(os.sep)
                sub_name = rel_parts[0].lower() if len(rel_parts) > 1 else ""
                # Skip news/social JSON entirely — they are not year-specific
                if fname_lower.endswith('.json'):
                    continue
                # For TXT/PDF: include only if the year can be confirmed from the filename
                if fname_lower.endswith(('.txt', '.pdf')):
                    year_ok = bool(
                        re.match(rf'^{year_str}_', fname)                          # "2023_Annual Report..."
                        or re.match(rf'^brsr_{year_str}_', fname_lower)            # "BRSR_2023_..."
                        or (sub_name in ('brsr', 'sustainability') and year_str in fname)
                    )
                    if not year_ok:
                        continue

            # Load TXT files (pre-extracted from PDFs)
            if fname_lower.endswith('.txt'):
                print(f"    📝 {fname}")
                # Priority: BRSR files > Annual Reports > Sustainability/News
                if "brsr" in fname_lower:
                    chunk_priority = 3
                elif "annual" in fname_lower or fname_lower[:4].isdigit():
                    chunk_priority = 2
                else:
                    chunk_priority = 1
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
                                    "type": "pdf",
                                    "priority": chunk_priority
                                })
                                summary["pdf_pages"] += 1
                    else:
                        # No page markers — treat entire file as one chunk
                        if content and len(content.strip()) > 50:
                            all_chunks.append({
                                "text": content,
                                "source": fname,
                                "type": "pdf",
                                "priority": chunk_priority
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
            # Apply priority multiplier: BRSR=3x, AnnualReport=2x, others=1x
            priority = chunk.get("priority", 1)
            scored.append((score * priority, chunk))

    scored.sort(key=lambda x: x[0], reverse=True)
    results = [s[1] for s in scored[:top_k]]

    # Guarantee: always include top BRSR chunks so numeric disclosures are never missed
    # Keep up to 5 highest-scoring BRSR pages even if they didn't make top_k
    brsr_scored = [(sc, ch) for sc, ch in scored if ch.get("priority", 1) == 3]
    brsr_top = [s[1] for s in brsr_scored[:5]]
    already_in = {id(c) for c in results}
    for c in brsr_top:
        if id(c) not in already_in:
            results.append(c)
            already_in.add(id(c))

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
        import re as _re

        if isinstance(raw_answer, str) and raw_answer.strip().lower().startswith("[error"):
            return {
                "value": "Not disclosed",
                "unit": "",
                "year": None,
                "previous_year_value": None,
                "source_detail": f"LLM provider error: {raw_answer.strip()[:240]}",
                "confidence": "low"
            }

        # Strip markdown code fences if present
        cleaned = raw_answer.strip()
        if cleaned.startswith("```"):
            lines = cleaned.split("\n")
            cleaned = "\n".join(lines[1:-1]) if len(lines) > 2 else cleaned

        # ── Try JSON parse first ────────────────────────────────────────────────
        try:
            parsed = json.loads(cleaned)
            if isinstance(parsed, dict):
                v = str(parsed.get("value", "")).strip().lower()
                if v in {"not available", "not applicable", "n/a", "na", "none", "", "null"}:
                    parsed["value"] = "Not disclosed"
                    parsed.setdefault("confidence", "low")
            return parsed
        except json.JSONDecodeError:
            pass

        # ── Try embedded JSON block inside prose ────────────────────────────────
        json_block = _re.search(r'\{[^{}]*"value"[^{}]*\}', cleaned, _re.DOTALL)
        if json_block:
            try:
                parsed = json.loads(json_block.group())
                return parsed
            except json.JSONDecodeError:
                pass

        # ── Extract numeric value from plain-text LLM answer ────────────────────
        number_match = _re.search(
            r'(?:is|was|were|:|\-|=)\s*([\d,]+(?:\.\d+)?\s*'
            r'(?:MWh|GWh|kWh|tCO2e|tCO₂e|tCO2|%|MW|GW|m³|m3|KL|ML|'
            r'tons?|MT|kg|litres?|L|\$|INR|Rs\.?|crore|lakh|million|billion)?)',
            cleaned, _re.IGNORECASE
        )
        if number_match:
            val = number_match.group(1).strip().rstrip(".,;")
            return {
                "value": val,
                "unit": "",
                "year": None,
                "previous_year_value": None,
                "source_detail": "Extracted from LLM plain-text response",
                "confidence": "medium",
            }

        # ── Check if LLM says data is missing ──────────────────────────────────
        low_phrases = [
            "not disclosed", "not available", "not found", "no data",
            "not reported", "not mentioned", "unable to find", "cannot find",
            "not provided", "no information", "not specified",
        ]
        if any(p in cleaned.lower() for p in low_phrases):
            return {
                "value": "Not disclosed",
                "unit": "",
                "year": None,
                "previous_year_value": None,
                "source_detail": "LLM indicated data not present in source",
                "confidence": "low",
            }

        # ── Return first 200 chars so we never lose a partial answer ───────────
        return {
            "value": cleaned[:200],
            "unit": "",
            "year": None,
            "previous_year_value": None,
            "source_detail": "LLM returned plain text (not JSON); value truncated",
            "confidence": "medium"
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

                    # Rate limit pause — 2s keeps paid Gemini under 30 RPM safely
                    time.sleep(2.0)

                results[pillar_key][sub_key] = {
                    "label": sub_label,
                    "metrics": answered_list
                }

        return results

    def run(self, questions_path, output_folder, year=None):
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
                "reporting_year": year,
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
        if year:
            out_filename = f"{sanitized}_ESG_Answers_{year}_{date_str}.json"
        else:
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
                     gemini_key=None, questions_path=None,
                     llm_chain=None, grok_key=None, openrouter_key=None,
                     year=None, yearly=False):
    """
    Main entry point for ESG analysis. Supports single-year and all-years modes.

    Args:
        company_name:   Company name (as used during scraping)
        base_folder:    Base downloads folder
        llm_type:       "gemini", "ollama", "grok", "openrouter", or "auto"
        gemini_key:     Gemini API key (or set GEMINI_API_KEY env var)
        questions_path: Path to esg_questions.json (auto-detected if None)
        llm_chain:      Comma-separated providers for failover
        grok_key:       xAI API key (or set XAI_API_KEY env var)
        openrouter_key: OpenRouter API key (or set OPENROUTER_API_KEY env var)
        year:           Single year to process (e.g. 2023); None = all data combined
        yearly:         If True, generate a separate ESG report per available year

    Returns:
        Single output path, list of paths (yearly mode), or None on failure.
    """
    print("\n" + "=" * 80)
    print("📊 ESG ANALYSIS — Extracting Metrics from Scraped Data")
    print("=" * 80)

    # ── Determine which years to process ────────────────────────────
    if yearly:
        years_to_process = detect_available_years(base_folder, company_name)
        if not years_to_process:
            print("\n  ❌ No annual report years detected in scraped data.")
            return None
        print(f"\n  📅 Yearly mode: {len(years_to_process)} years → {years_to_process}")
    elif year:
        years_to_process = [int(year)]
        print(f"\n  📅 Single-year mode: {year}")
    else:
        years_to_process = [None]   # None = load everything (original behaviour)

    # ── Initialise LLM once (reused across all years) ────────────────
    print(f"\n  🤖 Initializing LLM backend...")

    configured_chain = llm_chain or os.environ.get("LLM_CHAIN", "")
    provider_order = [p.strip().lower() for p in configured_chain.split(",") if p.strip()] if configured_chain else [llm_type.lower()]
    if provider_order == ["auto"]:
        provider_order = ["gemini", "grok", "openrouter", "ollama"]

    gemini_keys = _split_keys(gemini_key) or _split_keys(os.environ.get("GEMINI_API_KEY", ""))
    grok_keys = _split_keys(grok_key) or _split_keys(os.environ.get("XAI_API_KEY", ""))
    openrouter_keys = _split_keys(openrouter_key) or _split_keys(os.environ.get("OPENROUTER_API_KEY", ""))

    clients = []
    for provider in provider_order:
        if provider == "gemini":
            if not gemini_keys:
                continue
            for idx, key in enumerate(gemini_keys, start=1):
                clients.append((f"gemini#{idx}", GeminiRestLLM(api_key=key)))
        elif provider == "grok":
            if not grok_keys:
                continue
            for idx, key in enumerate(grok_keys, start=1):
                clients.append((
                    f"grok#{idx}",
                    OpenAICompatLLM(
                        api_key=key,
                        base_url="https://api.x.ai/v1",
                        model=GROK_MODEL,
                        provider_name="Grok",
                    )
                ))
        elif provider == "openrouter":
            if not openrouter_keys:
                continue
            for idx, key in enumerate(openrouter_keys, start=1):
                clients.append((
                    f"openrouter#{idx}",
                    OpenAICompatLLM(
                        api_key=key,
                        base_url="https://openrouter.ai/api/v1",
                        model=OPENROUTER_MODEL,
                        provider_name="OpenRouter",
                    )
                ))
        elif provider == "ollama":
            try:
                clients.append(("ollama", OllamaLLM()))
            except SystemExit:
                pass

    if not clients:
        print("  ❌ No valid LLM provider configured.")
        print("     Set at least one key: GEMINI_API_KEY / XAI_API_KEY / OPENROUTER_API_KEY")
        print("     Or run Ollama locally and include 'ollama' in --llm-chain.")
        return None

    llm = MultiProviderLLM(clients) if len(clients) > 1 else clients[0][1]
    print(f"  ✅ LLM providers ready: {', '.join([c[0] for c in clients])}")

    # 3. Resolve questions file
    if questions_path is None:
        questions_path = ESG_QUESTIONS_FILE
    if not os.path.exists(questions_path):
        print(f"  ❌ Questions file not found: {questions_path}")
        return None

    # ── Determine output folder ──────────────────────────────────────
    sanitized = _sanitize(company_name)
    output_folder = os.path.join(base_folder, "nseindia.com", sanitized)
    if not os.path.exists(output_folder):
        output_folder = os.path.join(base_folder, sanitized)

    # ── Process each year (or all data if year is None) ──────────────
    output_paths = []
    for yr in years_to_process:
        if yr:
            print(f"\n  {'─'*60}")
            print(f"  📅 Year: {yr}")
            print(f"  {'─'*60}")

        yr_label = f" [{yr}]" if yr else ""
        print(f"\n  📂 Loading scraped data for '{company_name}'{yr_label}...")
        all_chunks, summary = load_all_data(base_folder, company_name, year=yr)

        if not all_chunks:
            print(f"\n  ⚠️  No data found{yr_label}, skipping.")
            continue

        print(f"\n  ✅ Loaded {len(all_chunks)} text chunks from {summary['pdf_count']} PDFs, "
              f"{summary['news_items']} news, {summary['social_items']} social posts")

        analyzer = ESGAnalyzer(llm, all_chunks, company_name, summary)
        out = analyzer.run(questions_path, output_folder, year=yr)
        if out:
            output_paths.append(out)

    if not output_paths:
        return None
    return output_paths if yearly else output_paths[0]


# ═══════════════════════════════════════════════════════════════════════════════
#  CLI
# ═══════════════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="ESG Metric Extraction from Scraped Data")
    parser.add_argument("--company", required=True, help="Company name (as used during scraping)")
    parser.add_argument("--folder", default="downloads", help="Base downloads folder (default: downloads)")
    parser.add_argument("--llm", default="gemini",
                        help="Primary LLM backend: gemini | grok | openrouter | ollama | auto")
    parser.add_argument("--llm-chain", default=None,
                        help="Comma-separated failover providers, e.g. gemini,grok,openrouter,ollama")
    parser.add_argument("--gemini-key", help="Gemini API key(s), comma-separated, or set GEMINI_API_KEY")
    parser.add_argument("--grok-key", help="Grok/xAI API key(s), comma-separated, or set XAI_API_KEY")
    parser.add_argument("--openrouter-key", help="OpenRouter API key(s), comma-separated, or set OPENROUTER_API_KEY")
    parser.add_argument("--questions", default=None, help="Path to ESG questions JSON (default: esg_questions.json)")
    parser.add_argument("--year", type=int, default=None,
                        help="Generate ESG report for a specific year only (e.g. --year 2023)")
    parser.add_argument("--yearly", action="store_true",
                        help="Generate a separate ESG report for each available annual report year")

    args = parser.parse_args()

    output = run_esg_analysis(
        company_name=args.company,
        base_folder=args.folder,
        llm_type=args.llm,
        gemini_key=args.gemini_key,
        questions_path=args.questions,
        llm_chain=args.llm_chain,
        grok_key=args.grok_key,
        openrouter_key=args.openrouter_key,
        year=args.year,
        yearly=args.yearly,
    )

    if output:
        if isinstance(output, list):
            print(f"\n🎉 Done! {len(output)} yearly ESG reports saved:")
            for p in output:
                print(f"   📄 {p}")
        else:
            print(f"\n🎉 Done! Results saved to: {output}")
    else:
        print("\n❌ Analysis failed.")
        sys.exit(1)


if __name__ == "__main__":
    main()
