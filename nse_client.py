import requests
import time
import random
import re
import json
import os
from urllib.parse import quote

class NSEClient:
    BASE_URL = "https://www.nseindia.com/"
    # Using verified headers from exploration
    HEADERS = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
        "Accept-Encoding": "gzip, deflate",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
        "Sec-Fetch-User": "?1",
        "Cache-Control": "max-age=0",
    }

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update(self.HEADERS)
        self._initialized = False

    def _request_with_retry(self, url, method="GET", headers=None, timeout=30, max_retries=3):
        for attempt in range(max_retries):
            try:
                # Polite delay
                time.sleep(random.uniform(1.0, 3.0))
                
                response = self.session.request(method, url, headers=headers, timeout=timeout)
                
                if response.status_code == 429:
                    wait_time = (2 ** attempt) * 5
                    print(f"  [NSE Rate Limit] Sleeping {wait_time}s...")
                    time.sleep(wait_time)
                    continue
                elif response.status_code >= 500:
                    print(f"  [NSE Server Error] {response.status_code}. Retrying...")
                    time.sleep(2)
                    continue
                    
                return response
            except requests.RequestException as e:
                print(f"  [NSE Network Error] {e}")
                time.sleep(2)
        
        return None

    def _ensure_session(self):
        if not self._initialized:
            print("Initializing NSE session...")
            try:
                # Visit homepage to set cookies
                self.session.get(self.BASE_URL, timeout=30)
                self._initialized = True
            except requests.RequestException as e:
                print(f"Failed to initialize NSE session: {e}")

    def search_company(self, query):
        """
        Search for a company symbol on NSE.
        Returns list of {'symbol': 'RELIANCE', 'name': 'Reliance Industries Limited'}
        """
        self._ensure_session()
        search_url = f"https://www.nseindia.com/api/search/autocomplete?q={quote(query)}"
        
        # API requires slightly cleaner headers (no navigate mode)
        api_headers = {
            "Accept": "*/*",
            "Referer": "https://www.nseindia.com/",
            "Sec-Fetch-Dest": "empty",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Site": "same-origin",
            "X-Requested-With": "XMLHttpRequest"
        }
        
        try:
            print(f"Searching NSE for '{query}'...")
            # response = self.session.get(search_url, headers=api_headers, timeout=10)
            response = self._request_with_retry(search_url, headers=api_headers, timeout=15)
            
            if response and response.status_code == 200:
                try:
                    data = response.json()
                    results = []
                    for item in data.get('symbols', []):
                        results.append({
                            "symbol": item.get('symbol'),
                            "name": item.get('symbol_info')
                        })
                    return results
                except json.JSONDecodeError:
                    print(f"Error decoding JSON from NSE search. Response text preview: {response.text[:200]}")
                    return []
            else:
                print(f"NSE Search API returned status: {response.status_code}")
                return []

        except Exception as e:
            print(f"Error searching NSE: {e}")
            return []

    def get_annual_reports(self, symbol):
        """
        Get annual reports for a specific symbol.
        Note: NSE might not have a simple 'all annual reports' API open publically.
        We will try the 'corporate-filings' API.
        """
        self._ensure_session()
        
        # Endpoint structure based on exploration/common knowledge of NSE Hidden APIs
        # Try to fetch from corporate filings 
        # https://www.nseindia.com/api/corporate-filings?index=equities&symbol=RELIANCE&filing_type=annual-report
        
        # Note: 'filing_type' might need adjustment. Let's try to get all filings and filter.
        # Or look for specific annual report endpoint.
        
        # A common accessible endpoint for annual reports is:
        # https://www.nseindia.com/api/annual-reports?index=equities&symbol=RELIANCE
        # (This is hypothetical, if it fails we might need to parse HTML)
        
        # Let's try parsing the HTML page for annual reports first as it's more stable than hidden APIs
        # Page: https://www.nseindia.com/companies-listing/corporate-filings-annual-reports?symbol=RELIANCE&tabIndex=equity
        # Actually, that page likely loads data via API.
        
        # Let's try the suspected API:
        api_url = f"https://www.nseindia.com/api/annual-reports?index=equities&symbol={quote(symbol)}"
        
        api_headers = {
            "Accept": "*/*",
            "Referer": f"https://www.nseindia.com/companies-listing/corporate-filings-annual-reports?symbol={symbol}&tabIndex=equity",
            "X-Requested-With": "XMLHttpRequest"
        }

        try:
            print(f"Fetching annual reports for {symbol}...")
            # response = self.session.get(api_url, headers=api_headers, timeout=15)
            response = self._request_with_retry(api_url, headers=api_headers, timeout=20)
            
            reports = []
            if response and response.status_code == 200:
                data = response.json()
                # Data structure usually: { data: [ { 'companyName': ..., 'fileName': ..., 'fromYr': ... } ] }
                # Let's inspect data structure in real execution if possible, but for now write defensive parsing
                
                rows = data.get('data', [])
                for row in rows:
                    # Construct PDF Link
                    # Usually: https://nsearchives.nseindia.com/annual_reports/filename
                    filename = row.get('fileName')
                    year = row.get('fromYr')
                    
                    if filename:
                        if filename.startswith('http'):
                            url = filename
                        else:
                            url = f"https://nsearchives.nseindia.com/annual_reports/{filename}"
                            
                        reports.append({
                            "year": year,
                            "url": url,
                            "description": f"Annual Report {year}"
                        })
            elif response.status_code == 404:
                # Fallback to scraping the directory listing if API fails?
                # Or try another API "corporate-filings"
                print("Primary API failed (404). Trying generic filings API...")
                pass
            else:
                print(f"NSE Reports API returned status: {response.status_code}")
                
            return reports

        except Exception as e:
            print(f"Error fetching NSE reports: {e}")
            return []

    def get_brsr_reports(self, symbol):
        """
        Get BRSR (Business Responsibility and Sustainability Reports) for a specific symbol.
        """
        self._ensure_session()
        api_url = f"https://www.nseindia.com/api/annual-reports?index=equities&symbol={quote(symbol)}"
        
        api_headers = {
            "Accept": "*/*",
            "Referer": f"https://www.nseindia.com/companies-listing/corporate-filings-annual-reports?symbol={symbol}&tabIndex=equity",
            "X-Requested-With": "XMLHttpRequest"
        }

        try:
            print(f"Fetching BRSR reports for {symbol}...")
            # response = self.session.get(api_url, headers=api_headers, timeout=15)
            response = self._request_with_retry(api_url, headers=api_headers, timeout=20)
            
            reports = []
            if response and response.status_code == 200:
                data = response.json()
                rows = data.get('data', [])
                for row in rows:
                    filename = row.get('fileName')
                    year = row.get('fromYr')
                    
                    if filename:
                        is_brsr = 'brsr' in filename.lower() or 'business' in filename.lower()
                        
                        if filename.startswith('http'):
                            url = filename
                        else:
                            url = f"https://nsearchives.nseindia.com/annual_reports/{filename}"
                        
                        if is_brsr:
                             reports.append({
                                "year": year,
                                "url": url,
                                "description": f"BRSR {year} - {filename}"
                            })
            return reports

        except Exception as e:
            print(f"Error fetching NSE BRSR reports: {e}")
            return []


class NSEScraper:
    def __init__(self, data_dir):
        self.client = NSEClient()
        self.data_dir = data_dir

    def search_and_download_reports(self, company_name, limit=3):
        try:
            results = self.client.search_company(company_name)
            if not results:
                print(f"No NSE symbol found for {company_name}")
                return
            
            symbol = results[0]['symbol']
            print(f"Found NSE Symbol: {symbol}")
            
            reports = self.client.get_annual_reports(symbol)
            # Filter and sort
            reports.sort(key=lambda x: x['year'], reverse=True)
            to_download = reports[:limit]
            
            self._download_files(to_download, symbol, "AR")
        except Exception as e:
            print(f"NSEScraper Annual Report Error: {e}")

    def search_and_download_brsr(self, company_name, limit=3):
        try:
            results = self.client.search_company(company_name)
            if not results:
                return
            
            symbol = results[0]['symbol']
            reports = self.client.get_brsr_reports(symbol)
            
            reports.sort(key=lambda x: x['year'], reverse=True)
            to_download = reports[:limit]
            
            self._download_files(to_download, symbol, "BRSR")
        except Exception as e:
            print(f"NSEScraper BRSR Error: {e}")

    def _download_files(self, reports, symbol, prefix):
        # We can re-use the robust download_file from scraper.py if we import it, 
        # or implement similar logic here. Since scraper.py imports THIS file, 
        # importing scraper.py here would cause circular import.
        # So we duplicate the robust logic or move it to a utils file.
        # For safety in this session, I'll inline the robust logic here slightly simplified.
        
        for report in reports:
            year = report['year']
            url = report['url']
            filename = f"{symbol}_{prefix}_{year}.pdf"
            filename = re.sub(r'[<>:"/\\|?*]', '', filename)
            filepath = os.path.join(self.data_dir, filename)

            if os.path.exists(filepath):
                print(f"Skipping {filename}, already exists.")
                continue
            
            print(f"Downloading {filename} from {url}...")
            max_retries = 5
            for attempt in range(max_retries):
                try:
                    time.sleep(random.uniform(1.0, 3.0))
                    
                    r = self.client.session.get(url, stream=True, timeout=60)
                    
                    if r.status_code == 429:
                        wait_time = (2 ** attempt) * 5
                        print(f"  [Rate Limit] Sleeping {wait_time}s...")
                        time.sleep(wait_time)
                        continue
                        
                    if r.status_code == 200:
                        with open(filepath, 'wb') as f:
                            for chunk in r.iter_content(chunk_size=8192):
                                f.write(chunk)
                        print(f"Downloaded {filepath}")
                        break # Success
                    else:
                        print(f"Failed to download {url}, Status: {r.status_code}")
                        if r.status_code >= 500:
                            time.sleep(2)
                            continue
                        break # Non-retryable error
                        
                except Exception as e:
                    print(f"Download error {url}: {e}")
                    time.sleep(2)
            else:
                print(f"Failed to download {filename} after retries.")
