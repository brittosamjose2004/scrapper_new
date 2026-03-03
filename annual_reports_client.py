import requests
from bs4 import BeautifulSoup
import os
import re
import time
import random

class AnnualReportsClient:
    BASE_URL = "https://www.annualreports.com"

    def __init__(self):
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
            "Referer": "https://www.annualreports.com/",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8"
        }
        self.session = requests.Session()
        self.session.headers.update(self.headers)

    def _request_with_retry(self, url, timeout=30, max_retries=3):
        for attempt in range(max_retries):
            try:
                # Polite delay
                time.sleep(random.uniform(1.0, 3.0))
                
                response = self.session.get(url, timeout=timeout)
                
                if response.status_code == 429:
                    wait_time = (2 ** attempt) * 5
                    print(f"  [Rate Limit] Sleeping {wait_time}s...")
                    time.sleep(wait_time)
                    continue
                elif response.status_code >= 500:
                    print(f"  [Server Error] {response.status_code}. Retrying...")
                    time.sleep(2)
                    continue
                    
                return response
            except requests.RequestException as e:
                print(f"  [Network Error] {e}")
                time.sleep(2)
        return None

    def search_company(self, query):
        """
        Search for a company on annualreports.com
        Returns a list of dicts: {'name': 'Company Name', 'url': '/Company/company-name'}
        """
        search_url = f"{self.BASE_URL}/filter?q={query}"
        try:
            print(f"Searching annualreports.com for '{query}'...")
            response = self._request_with_retry(search_url, timeout=20)
            if not response:
                return []
                
            response.raise_for_status()
            
            # They might return JSON or HTML depending on endpoint
            # Actually /filter returns a JSON list usually for autocomplete
            # Let's check response type
            try:
                data = response.json()
                # data structure: [{"label":"Reliance Industries Limited","value":"/Company/reliance-industries-limited"}]
                results = []
                for item in data:
                    results.append({
                        "name": item.get("label"),
                        "url": self.BASE_URL + item.get("value")
                    })
                return results
            except ValueError:
                print(f"Error parsing JSON from search: {response.text[:100]}")
                return []
                
        except Exception as e:
            print(f"Error searching annualreports.com: {e}")
            return []

    def get_annual_reports(self, company_url):
        """
        Fetch annual report PDF links from a company page.
        Returns a list of dictionaries: {'year': 'YYYY', 'url': '...', 'description': '...'}
        """
        print(f"Fetching reports from {company_url}...")
        try:
            response = self._request_with_retry(company_url, timeout=20)
            if not response:
                return []
                
            response.raise_for_status()
            
            soup = BeautifulSoup(response.content, 'html.parser')
            reports = []
            
            # Locate the section containing reports. 
            # Usually strict structure: Year -> Link
            # Heuristic: Find links ending in .pdf or containing "View Annual Report"
            
            links = soup.find_all('a', href=True)
            for link in links:
                href = link['href']
                text = link.text.strip()
                
                # Filter for PDF links
                if '.pdf' in href.lower() or 'download' in text.lower():
                    # Try to extract year from text or common patterns
                    # Often text is "2023 Annual Report"
                    # Regex: Look for 19xx or 20xx. 
                    year_match = re.search(r'(19|20)\d{2}', text)
                    if not year_match:
                         # Try looking in the href (e.g., .../2023.pdf or ..._2023_...)
                         year_match = re.search(r'(19|20)\d{2}', href)
                    
                    year = year_match.group(0) if year_match else "Unknown"
                    
                    if not href.startswith("http"):
                        href = self.BASE_URL + href
                        
                    # unique check
                    if not any(r['url'] == href for r in reports):
                        reports.append({
                            "year": year,
                            "url": href,
                            "description": text
                        })
            
            # Sort by year descending
            reports.sort(key=lambda x: x['year'], reverse=True)
            return reports

        except requests.RequestException as e:
            print(f"Error fetching reports: {e}")
            return []

class AnnualReportsScraper:
    def __init__(self, data_dir):
        self.client = AnnualReportsClient()
        self.data_dir = data_dir

    def search_and_download(self, company_name, limit=3):
        results = self.client.search_company(company_name)
        if not results:
            print(f"No results found for {company_name}")
            return
        
        # Take the first best match
        best_match = results[0]
        company_url = best_match['url']
        print(f"Found Company: {best_match['name']} ({company_url})")

        reports = self.client.get_annual_reports(company_url)
        
        # Filter for recent ones
        # Assuming sorted desc
        reports_to_download = reports[:limit]
        
        for report in reports_to_download:
            year = report['year']
            url = report['url']
            
            # Filename
            filename = f"{company_name}_AR_{year}.pdf"
            # Sanitize filename
            filename = re.sub(r'[<>:"/\\|?*]', '', filename)
            filepath = os.path.join(self.data_dir, filename)

            if os.path.exists(filepath):
                print(f"Skipping {filename}, already exists.")
                continue

            print(f"Downloading {filename} from {url}...")
            try:
                r = requests.get(url, headers=self.client.headers, stream=True)
                r.raise_for_status()
                with open(filepath, 'wb') as f:
                    for chunk in r.iter_content(chunk_size=8192):
                        f.write(chunk)
                print(f"Downloaded {filepath}")
            except Exception as e:
                print(f"Failed to download {url}: {e}")
