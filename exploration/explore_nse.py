import requests
from bs4 import BeautifulSoup
import time

def explore():
    url = "https://www.nseindia.com/"
    # NSE blocks scraping heavily. Need to start a session and mimic a browser.
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
        "Accept": "*/*",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "Referer": "https://www.google.com/"
    }
    
    session = requests.Session()
    session.headers.update(headers)
    
    try:
        print(f"Fetching {url}...")
        response = session.get(url, timeout=10)
        response.raise_for_status()
        print(f"Main page Status: {response.status_code}")
        
        # NSE usually has separate API endpoints for data.
        # Annual reports are often under "Corporates" -> "Financial Results" or "Annual Reports".
        # Let's see if we can access the corporate search API.
        
        # Hypothetical API endpoint for company search
        company_search_url = "https://www.nseindia.com/api/search/autocomplete?q=Reliance"
        print(f"\nTrying search API: {company_search_url}")
        
        # NSE requires cookies from the main page to be present in API calls.
        api_response = session.get(company_search_url)
        print(f"API Status: {api_response.status_code}")
        if api_response.status_code == 200:
            print("API Response Preview:", api_response.text[:200])
            
        # Also try to specifically find where Annual Reports are.
        # Often: https://www.nseindia.com/companies-listing/corporate-filings-annual-reports
        reports_page = "https://www.nseindia.com/companies-listing/corporate-filings-annual-reports"
        print(f"\nFetching Reports Page: {reports_page}")
        reports_resp = session.get(reports_page)
        print(f"Reports Page Status: {reports_resp.status_code}")
        
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    explore()
