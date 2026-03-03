import requests
try:
    requests.get("https://www.google.com", timeout=5)
    print("Google Reached")
except Exception as e:
    print(f"Google Failed: {e}")
