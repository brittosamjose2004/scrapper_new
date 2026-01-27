import os
import json
import argparse
import requests
import time
import logging
from pdf_utils import extract_text_from_pdf

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Constants
QUESTIONS_FILE = "brsr_questions.json"
DOWNLOADS_DIR = "downloads"

class BRSRAnalyzer:
    def __init__(self, modal_url, questions_path):
        self.modal_url = modal_url
        self.questions = self.load_json(questions_path)
        
    def load_json(self, path):
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)

    def find_relevant_context(self, question, pages, top_k=3):
        """
        Simple keyword retrieval to find relevant pages for a question.
        """
        keywords = [w.lower() for w in question.split() if len(w) > 4]
        if not keywords:
            return pages[:top_k] # Return first few pages if no keywords

        scores = []
        for text in pages:
            score = 0
            text_lower = text.lower()
            for kw in keywords:
                if kw in text_lower:
                    score += 1
            scores.append((score, text))
        
        # Sort by score desc
        scores.sort(key=lambda x: x[0], reverse=True)
        
        # Return top K relevant pages
        return [s[1] for s in scores[:top_k] if s[0] > 0]

    def call_llm(self, prompt):
        """Call the Modal web endpoint"""
        try:
            payload = {"prompt": prompt}
            # Modal endpoints usually end with /
            response = requests.post(self.modal_url, json=payload, timeout=600)
            response.raise_for_status()
            try:
                return response.json().get("answer", "Error: No answer field")
            except json.JSONDecodeError:
                logger.error(f"Invalid JSON response: {response.text}")
                return f"Error: Invalid JSON. Raw: {response.text[:100]}"
        except Exception as e:
            logger.error(f"LLM Call failed: {e}")
            if 'response' in locals():
                 logger.error(f"Response Status: {response.status_code}")
                 logger.error(f"Response Text: {response.text}")
            return f"Error: {e}"

    def ask_llm(self, question, context_pages):
        context = "\n---\n".join(context_pages)
        # Truncate if too long (rough char count for ~8k tokens)
        if len(context) > 25000:
            context = context[:25000] + "...[truncated]"

        prompt = f"""You are an ESG analyst extracting data for a BRSR report.

CONTEXT from Annual Report:
{context}

QUESTION:
{question}

INSTRUCTIONS:
1. Answer the question specifically using the Context provided.
2. If data is tabular, strictly format it as a markdown table or structured list.
3. If the answer is NOT in the context, say "Data not found in relevant pages".
4. Be concise and factual.

ANSWER:"""
        return self.call_llm(prompt)

    def traverse_and_answer(self, node, pages_text):
        """Recursively walk the questions JSON"""
        if isinstance(node, dict):
            for k, v in node.items():
                if isinstance(v, list) and len(v) > 0:
                    # Check if it's a list of strings (Simple questions)
                    if isinstance(v[0], str):
                        answered = []
                        for q in v:
                            logger.info(f"Answering: {q[:50]}...")
                            context = self.find_relevant_context(q, pages_text)
                            if not context:
                                ans = "Data not found (Keyword mismatch)"
                            else:
                                ans = self.ask_llm(q, context)
                            answered.append({"question": q, "answer": ans})
                        node[k] = answered
                    
                    # Check if it's list of objects (Complex questions)
                    elif isinstance(v[0], dict) and "question_text" in v[0]:
                         for item in v:
                             q = item["question_text"]
                             logger.info(f"Answering: {q[:50]}...")
                             context = self.find_relevant_context(q, pages_text)
                             ans = self.ask_llm(q, context)
                             item["answer"] = ans
                             # Recurse if sub-questions exist
                             if "sub_questions" in item:
                                 self.traverse_and_answer(item["sub_questions"], pages_text)
                    else:
                        self.traverse_and_answer(v, pages_text)
                else:
                    self.traverse_and_answer(v, pages_text)
        elif isinstance(node, list):
            for item in node:
                self.traverse_and_answer(item, pages_text)

    def process_company(self, company_name):
        company_dir = os.path.join(DOWNLOADS_DIR, company_name)
        if not os.path.exists(company_dir):
            logger.error(f"Directory not found: {company_dir}")
            return

        for fname in os.listdir(company_dir):
            if fname.lower().endswith(".pdf"):
                logger.info(f"Processing Report: {fname}")
                pdf_path = os.path.join(company_dir, fname)
                
                # 1. Extract Text
                pages_text = extract_text_from_pdf(pdf_path)
                if not pages_text:
                    continue

                # 2. Clone Template
                report_data = json.loads(json.dumps(self.questions)) # Deep copy

                # 3. Answer Questions
                self.traverse_and_answer(report_data, pages_text)

                # 4. Save Result
                out_name = fname.replace(".pdf", "_BRSR_Extracted.json")
                out_path = os.path.join(company_dir, out_name)
                with open(out_path, 'w', encoding='utf-8') as f:
                    json.dump(report_data, f, indent=4)
                logger.info(f"Saved extraction to {out_path}")

def main():
    parser = argparse.ArgumentParser(description="Process Annual Reports with Modal LLM")
    parser.add_argument("--url", required=True, help="Modal Web Endpoint URL")
    parser.add_argument("--company", required=True, help="Company directory name in downloads/")
    args = parser.parse_args()

    analyzer = BRSRAnalyzer(args.url, QUESTIONS_FILE)
    analyzer.process_company(args.company)

if __name__ == "__main__":
    main()
