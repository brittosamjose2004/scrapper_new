import pdfplumber
import logging

logger = logging.getLogger(__name__)

def extract_text_from_pdf(pdf_path):
    """
    Extracts text from a PDF file.
    Returns a list of strings, where each string is the text of a page.
    """
    pages_text = []
    try:
        with pdfplumber.open(pdf_path) as pdf:
            for i, page in enumerate(pdf.pages):
                text = page.extract_text()
                if text:
                    # Optional: Clean text here (remove headers/footers if pattern known)
                    pages_text.append(text)
                else:
                    pages_text.append("") # Keep index alignment
        logger.info(f"Extracted {len(pages_text)} pages from {pdf_path}")
        return pages_text
    except Exception as e:
        logger.error(f"Error extracting PDF {pdf_path}: {e}")
        return []
