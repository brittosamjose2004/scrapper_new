import requests
import time

def explore_nse_advanced():
    # NSE India is known for strict anti-scraping.
    # We need to mimic a browser very closely.
    
    base_url = "https://www.nseindia.com/"
    api_url = "https://www.nseindia.com/api/search/autocomplete?q=Reliance"
    
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
        "Sec-Fetch-User": "?1",
        "Cache-Control": "max-age=0",
    }
    
    session = requests.Session()
    session.headers.update(headers)
    
    try:
        print("1. Visiting Homepage to initialize cookies...")
        resp = session.get(base_url, timeout=20)
        print(f"Homepage Status: {resp.status_code}")
        
        if resp.status_code == 200:
            print("Cookies set:", session.cookies.get_dict().keys())
            
            # Now try the API with slightly modified headers for fetch
            api_headers = headers.copy()
            api_headers.update({
                "Accept": "*/*",
                "Referer": "https://www.nseindia.com/",
                "Sec-Fetch-Dest": "empty",
                "Sec-Fetch-Mode": "cors",
                "Sec-Fetch-Site": "same-origin",
                "X-Requested-With": "XMLHttpRequest"
            })
            
            print(f"\n2. Trying API: {api_url}")
            api_resp = session.get(api_url, headers=api_headers, timeout=20)
            print(f"API Status: {api_resp.status_code}")
            if api_resp.status_code == 200:
                print("API Success! Sample data:")
                print(api_resp.text[:500])
            else:
                print("API Failed.")
                
            # Try finding annual reports page
            rp_url = "https://www.nseindia.com/companies-listing/corporate-filings-annual-reports"
            print(f"\n3. Visiting Annual Reports Page: {rp_url}")
            rp_resp = session.get(rp_url, headers=headers)
            print(f"Reports Page Status: {rp_resp.status_code}")
            
    except Exception as e:
        print(f"Error during NSE exploration: {e}")

if __name__ == "__main__":
    explore_nse_advanced()
