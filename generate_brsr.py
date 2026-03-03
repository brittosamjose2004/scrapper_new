import os
import json
import argparse
import requests
import re
from datetime import datetime

# Try imports
try:
    from pypdf import PdfReader
except ImportError:
    print("Error: pypdf not installed. Please run 'pip install pypdf'")
    exit(1)

class BRSRGenerator:
    def __init__(self, questions_path, llm_url="http://localhost:11434", model="gemma:7b"):
        self.questions_path = questions_path
        self.llm_url = llm_url
        self.model = model
        
        # Load Questions
        with open(questions_path, 'r', encoding='utf-8') as f:
            self.schema = json.load(f)

    def extract_text_from_pdf(self, pdf_path):
        """
        Extracts text from PDF. simple page iteration.
        Returns list of pages: [{'page': 1, 'text': '...'}, ...]
        """
        print(f"  Reading PDF: {os.path.basename(pdf_path)}...")
        reader = PdfReader(pdf_path)
        pages = []
        for i, page in enumerate(reader.pages):
            text = page.extract_text()
            if text:
                pages.append({'page': i+1, 'text': text})
        return pages

    def find_relevant_context(self, question, pages, window=3):
        """
        Simple keyword search to find relevant pages.
        """
        # Extract keywords (stopwords removal is better, but simple split works for now)
        words = [w.lower() for w in question.split() if len(w) > 4]
        
        relevant_texts = []
        scores = []
        
        for p in pages:
            score = 0
            text_lower = p['text'].lower()
            for w in words:
                if w in text_lower:
                    score += 1
            
            if score > 0:
                scores.append((score, p['text']))
        
        # Sort by score and take top K
        scores.sort(key=lambda x: x[0], reverse=True)
        top_pages = [s[1] for s in scores[:window]]
        
        return "\n---\n".join(top_pages)

    def query_llm(self, context, question):
        """
        Sends prompt to LLM (Ollama format).
        """
        prompt = f"""You are an expert ESG analyst. 
        Use the following Annual Report excerpt to answer the BRSR question.
        If the information is not present, state "Not disclosed".
        
        CONTEXT:
        {context}
        
        QUESTION:
        {question}
        
        ANSWER (Concise):"""
        
        # API call depending on provider. Assuming Ollama /api/generate
        try:
            payload = {
                "model": self.model,
                "prompt": prompt,
                "stream": False
            }
            # Adjust endpoint if needed
            url = f"{self.llm_url}/api/generate"
            resp = requests.post(url, json=payload, timeout=60)
            
            if resp.status_code == 200:
                return resp.json().get('response', '').strip()
            else:
                return f"[Error LLM: {resp.status_code}]"
        except Exception as e:
            return f"[Connection Error: {e}]"

    def process_node(self, node, pages):
        """
        Recursively process the JSON structure.
        """
        if isinstance(node, dict):
            new_node = {}
            for k, v in node.items():
                if k == "questions" and isinstance(v, list):
                    # Process list of questions
                    new_q_list = []
                    for q in v:
                        if isinstance(q, str):
                            # Simple string question
                            context = self.find_relevant_context(q, pages)
                            answer = self.query_llm(context, q)
                            print(f"    Q: {q[:40]}... -> A: {answer[:40]}...")
                            new_q_list.append({"question": q, "answer": answer})
                        elif isinstance(q, dict) and "question_text" in q:
                             # Complex question object
                             q_text = q["question_text"]
                             context = self.find_relevant_context(q_text, pages)
                             answer = self.query_llm(context, q_text)
                             print(f"    Q: {q_text[:40]}... -> A: {answer[:40]}...")
                             # Clone dict and add answer
                             q_filled = q.copy()
                             q_filled["generated_answer"] = answer
                             new_q_list.append(q_filled)
                        else:
                            new_q_list.append(self.process_node(q, pages))
                    new_node[k] = new_q_list
                else:
                    new_node[k] = self.process_node(v, pages)
            return new_node
        elif isinstance(node, list):
            return [self.process_node(item, pages) for item in node]
        else:
            return node

    def generate_report(self, company, year, pdf_path, output_folder):
        print(f"\nGeneratring BRSR for {company} ({year})...")
        
        # 1. Read PDF
        pages = self.extract_text_from_pdf(pdf_path)
        if not pages:
            print("  Empty or unreadable PDF.")
            return

        # 2. Process Questions
        filled_data = self.process_node(self.schema, pages)
        
        # 3. Save
        fname = f"{year}_BRSR_Filled.json"
        out_path = os.path.join(output_folder, fname)
        with open(out_path, 'w', encoding='utf-8') as f:
            json.dump(filled_data, f, indent=2)
        print(f"  Saved: {out_path}")

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--company", required=True)
    parser.add_argument("--folder", help="Folder containing PDFs", default="downloads")
    parser.add_argument("--questions", default=r"C:\Users\britt\OneDrive\Desktop\brsr_questions.json")
    parser.add_argument("--llm_url", default="http://localhost:11434")
    parser.add_argument("--model", default="gemma:7b")
    
    args = parser.parse_args()
    
    # Resolve company folder
    # logic to find folder...
    # simple recursive search for PDFs?
    
    comp_folder = os.path.join(args.folder, args.company) # Simple assumption
    # If not found, search?
    if not os.path.exists(comp_folder):
         # Logic to find fuzzy match
         pass
         
    gen = BRSRGenerator(args.questions, args.llm_url, args.model)
    
    # Iterate PDFs in folder
    for root, dirs, files in os.walk(comp_folder):
        for file in files:
            if file.lower().endswith('.pdf'):
                # Heuristic to find year?
                # Filename: 2025_AnnualReport.pdf
                year_match = re.search(r"20\d{2}", file)
                year = year_match.group(0) if year_match else "UnknownYear"
                
                pdf_path = os.path.join(root, file)
                gen.generate_report(args.company, year, pdf_path, root)

if __name__ == "__main__":
    main()
