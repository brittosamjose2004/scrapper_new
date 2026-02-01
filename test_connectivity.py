import requests
import time

url = "https://www.annualreports.com/filter?q=Reliance"
headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.annualreports.com/"
}

print(f"Testing connectivity to {url}...")
try:
    start = time.time()
    response = requests.get(url, headers=headers, timeout=10)
    print(f"Status Code: {response.status_code}")
    print(f"Time taken: {time.time() - start:.2f}s")
    print(f"Response preview: {response.text[:200]}")
except Exception as e:
    print(f"Connection failed: {e}")
