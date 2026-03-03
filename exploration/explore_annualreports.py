import requests
from bs4 import BeautifulSoup

def explore():
    url = "https://www.annualreports.com/Browse/Exchange"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
    }
    try:
        print(f"Fetching {url}...")
        response = requests.get(url, headers=headers)
        response.raise_for_status()
        print(f"Status: {response.status_code}")
        
        soup = BeautifulSoup(response.text, 'html.parser')
        
        # Check for search bar form
        search_form = soup.find('form', id='searchForm') # Guessing ID
        if not search_form:
            search_form = soup.find('form')
            
        print("Forms found:", len(soup.find_all('form')))
        for form in soup.find_all('form'):
            print(f"Form action: {form.get('action')}, method: {form.get('method')}")

        # specific search test
        search_url = "https://www.annualreports.com/Search" # hypothetical
        print(f"Trying direct search for 'Apple'...")
        # Usually search is a GET or POST to some endpoint. 
        # Let's try to just find where the search bar goes.
        
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    explore()
