#!/usr/bin/env python3
"""
Explore NSE BRSR API endpoints
"""
import requests
import time
import json
from bs4 import BeautifulSoup

def explore_brsr():
    session = requests.Session()
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }
    session.headers.update(headers)
    
    # Initialize session
    print("Initializing NSE session...")
    session.get("https://www.nseindia.com/", timeout=15)
    time.sleep(2)
    
    symbol = "RELIANCE"
    
    # The page URL from user
    page_url = f"https://www.nseindia.com/companies-listing/corporate-filings-bussiness-sustainabilitiy-reports?symbol={symbol}&tabIndex=equity"
    
    print(f"\nFetching page: {page_url}")
    response = session.get(page_url, timeout=15)
    
    # Parse the page
    soup = BeautifulSoup(response.text, 'html.parser')
    
    # Look for table or data elements
    tables = soup.find_all('table')
    print(f"\nFound {len(tables)} tables on page")
    
    # Look for links to PDF files
    links = soup.find_all('a', href=True)
    pdf_links = [link for link in links if '.pdf' in link.get('href', '').lower()]
    print(f"Found {len(pdf_links)} PDF links")
    
    for link in pdf_links[:5]:
        print(f"  {link.get('href')[:100]}")
    
    # Try common API patterns
    print("\n\nTrying API endpoints...")
    
    api_variations = [
        f"https://www.nseindia.com/api/corporate-sustainability?symbol={symbol}&index=equities",
        f"https://www.nseindia.com/api/bsr?symbol={symbol}&index=equities",
        f"https://www.nseindia.com/api/bsr-reports?symbol={symbol}&index=equities",
        f"https://www.nseindia.com/api/business-sustainability-reports?symbol={symbol}",
        f"https://www.nseindia.com/api/corporates-bussiness-sustainability?index=equities&symbol={symbol}",
    ]
    
    api_headers = {
        "Accept": "*/*",
        "Referer": page_url,
        "X-Requested-With": "XMLHttpRequest"
    }
    
    for url in api_variations:
        try:
            time.sleep(1)
            resp = session.get(url, headers=api_headers, timeout=10)
            if resp.status_code == 200:
                print(f"\n✓ SUCCESS: {url}")
                try:
                    data = resp.json()
                    print(f"  Records: {len(data.get('data', []))}")
                    if data.get('data'):
                        print(f"  Sample: {json.dumps(data['data'][0], indent=4)}")
                except:
                    print(f"  Response: {resp.text[:200]}")
            else:
                print(f"✗ {resp.status_code}: {url}")
        except Exception as e:
            print(f"✗ Error: {url} - {e}")

if __name__ == "__main__":
    explore_brsr()
