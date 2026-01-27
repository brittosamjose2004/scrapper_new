import requests
from bs4 import BeautifulSoup

def explore():
    url = "https://www.annualreports.com/Companies"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
    }
    try:
        # Search typically sends a parameter. Let's try `?q=Apple` or similar on valid endpoints.
        # Looking at previous output "Form action: /Companies", let's try searching there.
        # Actually, usually search bars have an input name like 'search' or 'q'.
        
        # In a real browser, I'd type 'Apple' and hit enter. 
        # If I can't see the input name, I'll guess 'search'.
        
        payload = {'search': 'Apple'} 
        print(f"Searching for 'Apple' at {url} with payload {payload}...")
        response = requests.get(url, params=payload, headers=headers)
        
        # If it redirects or shows results, we are good.
        print(f"Status: {response.status_code}")
        print(f"URL after: {response.url}")
        
        if "Apple" in response.text:
            print("Found 'Apple' in response text!")
            
        soup = BeautifulSoup(response.text, 'html.parser')
        # Look for links to companies
        links = soup.find_all('a', href=True)
        for link in links[:20]: # Print first 20 links
            if 'Company' in link['href']:
                print(f"Found Company Link: {link['href']}")
                
        # Also try accessing a specific company page directly if we can guess the slug
        # https://www.annualreports.com/Company/apple-inc
        try_url = "https://www.annualreports.com/Company/apple-inc"
        print(f"\nTrying direct access: {try_url}")
        resp2 = requests.get(try_url, headers=headers)
        if resp2.status_code == 200:
            print("Direct access successful!")
            soup2 = BeautifulSoup(resp2.text, 'html.parser')
            # Look for PDF links
            pdfs = soup2.find_all('a', href=True)
            for pdf in pdfs:
                if '.pdf' in pdf['href'] or 'Click to view' in pdf.text:
                    print(f"Found PDF/Report link: {pdf['href']} - {pdf.text.strip()}")
        
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    explore()
