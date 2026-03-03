"""
PDF to TXT Extractor

Converts all downloaded PDFs into plain text files (.txt).
Handles ZIP-wrapped PDFs (files that are actually ZIPs containing a PDF inside).
Supports both pypdf and pdfplumber for extraction.

Usage:
  python pdf_to_txt.py --folder downloads
  python pdf_to_txt.py --folder downloads --company "Tata Steel"
"""

import os
import sys
import zipfile
import tempfile
import argparse
import logging

# Fix Windows console encoding
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding='utf-8')
        sys.stderr.reconfigure(encoding='utf-8')
    except Exception:
        pass

# PDF extraction library
try:
    from pypdf import PdfReader
    PDF_LIB = "pypdf"
except ImportError:
    PdfReader = None
    PDF_LIB = None

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


def is_zip_file(filepath):
    """Check if a file is actually a ZIP archive (regardless of extension)."""
    try:
        with open(filepath, 'rb') as f:
            magic = f.read(4)
            return magic == b'PK\x03\x04'
    except Exception:
        return False


def extract_pdf_from_zip(zip_path):
    """Extract the first PDF found inside a ZIP file. Returns path to temp PDF or None."""
    try:
        with zipfile.ZipFile(zip_path, 'r') as zf:
            pdf_files = [f for f in zf.namelist() if f.lower().endswith('.pdf')]
            if not pdf_files:
                logger.warning(f"    No PDF found inside ZIP: {os.path.basename(zip_path)}")
                return None

            # Extract the first PDF to a temp file
            pdf_name = pdf_files[0]
            temp_dir = tempfile.mkdtemp()
            extracted_path = zf.extract(pdf_name, temp_dir)
            logger.info(f"    📦 Extracted '{pdf_name}' from ZIP")
            return extracted_path
    except Exception as e:
        logger.warning(f"    Failed to extract ZIP {os.path.basename(zip_path)}: {e}")
        return None


def extract_text_from_pdf(pdf_path):
    """Extract all text from a PDF file. Returns list of (page_num, text) tuples."""
    if PdfReader is None:
        logger.error("pypdf not installed. Run: pip install pypdf")
        return []

    try:
        reader = PdfReader(pdf_path)
        pages = []
        for i, page in enumerate(reader.pages):
            text = page.extract_text()
            if text and len(text.strip()) > 30:
                pages.append((i + 1, text))
        return pages
    except Exception as e:
        logger.warning(f"    Failed to read PDF: {e}")
        return []


def process_single_pdf(pdf_path, output_dir=None):
    """
    Convert a single PDF (or ZIP-wrapped PDF) to a TXT file.
    Returns the output TXT path, or None if failed.
    """
    filename = os.path.basename(pdf_path)
    parent_dir = os.path.dirname(pdf_path)
    
    if output_dir is None:
        output_dir = parent_dir

    # Determine output filename
    txt_filename = os.path.splitext(filename)[0] + ".txt"
    txt_path = os.path.join(output_dir, txt_filename)

    # Skip if TXT already exists and is non-empty
    if os.path.exists(txt_path) and os.path.getsize(txt_path) > 100:
        logger.info(f"  ⏭️  Already extracted: {txt_filename}")
        return txt_path

    actual_pdf = pdf_path
    temp_pdf = None

    # Check if the "PDF" is actually a ZIP file
    if is_zip_file(pdf_path):
        logger.info(f"  📦 {filename} is a ZIP file, extracting PDF inside...")
        temp_pdf = extract_pdf_from_zip(pdf_path)
        if temp_pdf is None:
            return None
        actual_pdf = temp_pdf

    # Extract text from the PDF
    pages = extract_text_from_pdf(actual_pdf)

    # Clean up temp file
    if temp_pdf and os.path.exists(temp_pdf):
        try:
            os.remove(temp_pdf)
            os.rmdir(os.path.dirname(temp_pdf))
        except Exception:
            pass

    if not pages:
        logger.warning(f"  ❌ No text extracted from: {filename}")
        return None

    # Write text to file
    with open(txt_path, 'w', encoding='utf-8') as f:
        f.write(f"# Extracted from: {filename}\n")
        f.write(f"# Total pages with text: {len(pages)}\n")
        f.write("=" * 80 + "\n\n")

        for page_num, text in pages:
            f.write(f"--- PAGE {page_num} ---\n")
            f.write(text)
            f.write("\n\n")

    total_chars = sum(len(t) for _, t in pages)
    logger.info(f"  ✅ {txt_filename} ({len(pages)} pages, {total_chars:,} chars)")
    return txt_path


def process_all_pdfs(base_folder, company_name=None):
    """
    Convert all PDFs in a folder tree to TXT files.
    
    Args:
        base_folder: Base downloads folder
        company_name: Optional - only process this company's PDFs
    
    Returns:
        dict with stats: {"processed": N, "skipped": N, "failed": N, "txt_files": [...]}
    """
    print("\n" + "=" * 80)
    print("📄 PDF → TXT EXTRACTION")
    print("=" * 80)

    stats = {"processed": 0, "skipped": 0, "failed": 0, "txt_files": []}

    if not os.path.exists(base_folder):
        print(f"\n  ❌ Folder not found: {base_folder}")
        return stats

    # Find all PDF files
    pdf_files = []
    for root, dirs, files in os.walk(base_folder):
        # If company_name specified, filter to matching folders
        if company_name:
            sanitized = company_name.lower().strip()
            rel_path = os.path.relpath(root, base_folder).lower()
            if sanitized not in rel_path and rel_path != '.':
                continue

        for fname in sorted(files):
            if fname.lower().endswith('.pdf'):
                pdf_files.append(os.path.join(root, fname))

    if not pdf_files:
        print(f"\n  ❌ No PDF files found in {base_folder}")
        if company_name:
            print(f"     (filtered for company: {company_name})")
        return stats

    print(f"\n  Found {len(pdf_files)} PDF files to process\n")

    for i, pdf_path in enumerate(pdf_files, 1):
        rel = os.path.relpath(pdf_path, base_folder)
        print(f"  [{i}/{len(pdf_files)}] {rel}")

        result = process_single_pdf(pdf_path)
        if result:
            stats["txt_files"].append(result)
            stats["processed"] += 1
        elif os.path.exists(os.path.splitext(pdf_path)[0] + ".txt"):
            stats["skipped"] += 1
        else:
            stats["failed"] += 1

    print(f"\n  {'=' * 60}")
    print(f"  ✅ Extraction Complete!")
    print(f"     Processed: {stats['processed']}")
    print(f"     Skipped (already done): {stats['skipped']}")
    print(f"     Failed: {stats['failed']}")
    print(f"     TXT files ready: {len(stats['txt_files'])}")
    print(f"  {'=' * 60}\n")

    return stats


def main():
    parser = argparse.ArgumentParser(description="Convert downloaded PDFs to TXT files")
    parser.add_argument("--folder", default="downloads", help="Base downloads folder (default: downloads)")
    parser.add_argument("--company", default=None, help="Only process PDFs for this company")

    args = parser.parse_args()

    stats = process_all_pdfs(args.folder, args.company)

    if stats["processed"] + stats["skipped"] > 0:
        print(f"🎉 Done! TXT files are saved next to the original PDFs.")
    else:
        print("❌ No files processed.")
        sys.exit(1)


if __name__ == "__main__":
    main()
