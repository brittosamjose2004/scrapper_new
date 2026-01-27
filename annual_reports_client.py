import requests
from bs4 import BeautifulSoup
import os
import re

class AnnualReportsClient:
    BASE_URL = "https://www.annualreports.com"
    SEARCH_URL = "https://www.annualreports.com/Companies"

    def __init__(self):
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
        }

    def search_company(self, query):
        """
        Search for a company and return its details (name, url).
        """
        print(f"Searching annualreports.com for '{query}'...")
        params = {'search': query}
        try:
            response = requests.get(self.SEARCH_URL, params=params, headers=self.headers)
            response.raise_for_status()
            
            # If redirected directly to company page
            if "/Company/" in response.url:
                return [{"name": query, "url": response.url}]

            soup = BeautifulSoup(response.text, 'html.parser')
            results = []
            
            # This selector depends on the actual search result page structure
            # Based on exploration, it seems to list companies.
            # We'll look for links containing /Company/
            links = soup.find_all('a', href=re.compile(r"/Company/"))
            
            seen_urls = set()
            for link in links:
                url = link['href']
                if not url.startswith("http"):
                    url = self.BASE_URL + url
                
                name = link.text.strip()
                if url not in seen_urls and name:
                    results.append({"name": name, "url": url})
                    seen_urls.add(url)
            
            return results

        except requests.RequestException as e:
            print(f"Error searching annualreports.com: {e}")
            return []

    def get_annual_reports(self, company_url):
        """
        Fetch annual report PDF links from a company page.
        Returns a list of dictionaries: {'year': 'YYYY', 'url': '...', 'description': '...'}
        """
        print(f"Fetching reports from {company_url}...")
        try:
            response = requests.get(company_url, headers=self.headers)
            response.raise_for_status()
            
            soup = BeautifulSoup(response.text, 'html.parser')
            reports = []
            
            # Locate the section containing reports. 
            # Usually strict structure: Year -> Link
            # Heuristic: Find links ending in .pdf or containing "View Annual Report"
            
            # Typically structure might be hidden in divs.
            # We will look for all 'a' tags that look like reports.
            
            links = soup.find_all('a', href=True)
            for link in links:
                href = link['href']
                text = link.text.strip()
                
                # Filter for PDF links
                if '.pdf' in href.lower() or 'download' in text.lower():
                    # Debug: print text and href
                    # print(f"Analyzing: Text='{text}', Href='{href}'")
                    
                    # Try to extract year from text or common patterns
                    # Often text is "2023 Annual Report"
                    # Regex: Look for 19xx or 20xx. 
                    year_match = re.search(r'(19|20)\d{2}', text)
                    if not year_match:
                         # Try looking in the href (e.g., .../2023.pdf or ..._2023_...)
                         year_match = re.search(r'(19|20)\d{2}', href)
                    
                    year = year_match.group(0) if year_match else "Unknown"
                    
                    # If unknown, try to look at the previous sibling?? 
                    # Sometimes the year is in a separate column. 
                    # But for now let's just log if unknown.
                    if year == "Unknown":
                        print(f"  [Warning] Could not extract year from: Text='{text}', Href='{href}'")

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
