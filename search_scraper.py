import requests
from bs4 import BeautifulSoup
import re
import os
import urllib.parse

class SearchScraper:
    def __init__(self):
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
        }

    def search_and_download_pdfs(self, company, report_type, download_folder, limit=3):
        """
        Searches using DuckDuckGo HTML (no API key needed) for PDFs.
        Query: "{company} {report_type} filetype:pdf"
        """
        query = f"{company} {report_type} filetype:pdf"
        print(f"\nSearching for: {query}")
        
        # DuckDuckGo HTML Search
        url = "https://html.duckduckgo.com/html/"
        data = {'q': query}
        
        try:
            response = requests.post(url, data=data, headers=self.headers)
            response.raise_for_status()
            
            soup = BeautifulSoup(response.text, 'html.parser')
            results = soup.find_all('a', class_='result__a')
            
            count = 0
            for link in results:
                if count >= limit: break
                
                href = link['href']
                title = link.text.strip()
                
                # Check directly if it looks like a PDF url
                # DuckDuckGo sometimes wraps URLs, but usually not in HTML version or easy to extract
                
                # Check for relevance: Company name should be in title or URL
                # Simple check: Split company name into words and ensure at least one significant word is present
                company_words = [w.lower() for w in company.split() if len(w) > 3]
                if not company_words: company_words = [company.lower()]
                
                is_relevant = any(w in title.lower() for w in company_words)
                
                if not is_relevant:
                    print(f"    [Skipping] Irrelevant title: {title}")
                    continue

                if '.pdf' in href.lower():
                     # Construct a filename
                     # sanitize
                    safe_title = "".join([c for c in title if c.isalpha() or c.isdigit() or c in (' ', '-', '_')]).strip()
                    if not safe_title: safe_title = "report"
                    
                    filename = f"{report_type}_{safe_title[:50]}.pdf"
                    
                    # Verify it's actually a PDF by HEAD request? 
                    # Or just try to download it.
                    
                    print(f"  Found PDF candidate: {title}")
                    self.download_file(href, download_folder, filename)
                    count += 1
                
        except Exception as e:
            print(f"Error searching for {report_type}: {e}")

    def download_file(self, url, folder, filename):
        if not os.path.exists(folder):
            os.makedirs(folder)
            
        path = os.path.join(folder, filename)
        if os.path.exists(path):
            print(f"    [Skipping] {filename} (exists)")
            return

        print(f"    [Downloading] {filename}...")
        try:
            # Need strict headers often for these corporate sites
            response = requests.get(url, headers=self.headers, stream=True, timeout=30)
            
            # Check content type
            ct = response.headers.get('Content-Type', '').lower()
            if 'pdf' not in ct and 'application/octet-stream' not in ct:
                print(f"    [Skipping] Not a PDF (Content-Type: {ct})")
                return

            with open(path, 'wb') as f:
                for chunk in response.iter_content(chunk_size=8192):
                    f.write(chunk)
            print(f"    [Done]")
        except Exception as e:
            print(f"    [Error] {e}")
