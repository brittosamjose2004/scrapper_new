import requests
import xml.etree.ElementTree as ET
from bs4 import BeautifulSoup
from urllib.parse import quote
import os
import json
from datetime import datetime
import time
import random

class NewsScraper:
    def __init__(self):
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
        }

    def _extract_text(self, url):
        """
        Visits the URL and attempts to extract main article text.
        """
        try:
            # Random delay to be polite
            time.sleep(random.uniform(0.5, 1.5))
            # Follow redirects is default, but ensure headers help avoid blocks
            response = requests.get(url, headers=self.headers, timeout=15, allow_redirects=True)
            
            # Check if we are stuck on a Google consent/redirect page
            if "consent.google.com" in response.url:
                return "" # Can't bypass easily without browser
                
            response.raise_for_status()
            
            soup = BeautifulSoup(response.text, 'html.parser')
            
            # Clean pollution
            for script in soup(["script", "style", "nav", "footer", "header", "noscript", "iframe"]):
                script.decompose()
            
            # Strategy 1: Meta Description (High quality fallback)
            meta_desc = ""
            meta_tag = soup.find("meta", attrs={"name": "description"}) or soup.find("meta", attrs={"property": "og:description"})
            if meta_tag:
                meta_desc = meta_tag.get("content", "").strip()

            # Strategy 2: <article> or <p> tags
            text_blocks = []
            article = soup.find('article')
            if article:
                text_blocks = [p.get_text().strip() for p in article.find_all('p')]
            else:
                text_blocks = [p.get_text().strip() for p in soup.find_all('p')]
            
            # Filter empty or short lines
            clean_blocks = [t for t in text_blocks if len(t) > 20]
            full_text = " ".join(clean_blocks)
            
            # Strategy 3: Text Density (Fallback if <p> tags failed)
            if len(full_text) < 200:
                # Find the div with the most text
                max_len = 0
                best_text = ""
                for div in soup.find_all('div'):
                    # Get direct text or minimal nesting? 
                    # Simplify: just get text
                    t = div.get_text(" ", strip=True)
                    if len(t) > max_len:
                        max_len = len(t)
                        best_text = t
                
                if len(best_text) > len(full_text) and len(best_text) > 100:
                    full_text = best_text[:5000] # Cap length to avoid dumping huge garbage

            # Return best result
            return full_text if len(full_text) > 50 else meta_desc
            
        except Exception as e:
            # print(f"    [Error] Content extract failed for {url}: {e}")
            return ""

    def fetch_news(self, company, limit=5):
        """
        Fetches news RSS and then scrapes FULL TEXT.
        """
        print(f"\nFetching news and extracting FULL CONTENT for '{company}'...")
        rss_url = f"https://news.google.com/rss/search?q={quote(company)}+when:1y&hl=en-IN&gl=IN&ceid=IN:en"
        
        try:
            response = requests.get(rss_url, headers=self.headers, timeout=15)
            response.raise_for_status()
            
            # Parse XML
            root = ET.fromstring(response.content)
            items = root.findall('.//item')
            
            news_items = []
            count = 0
            for item in items:
                if count >= limit: break
                
                title = item.find('title').text
                link = item.find('link').text
                pubDate = item.find('pubDate').text
                
                # Get description/snippet from RSS as fallback
                description_tag = item.find('description')
                description = ""
                if description_tag is not None:
                    # Description is usually HTML, strip tags
                    d_text = description_tag.text or ""
                    soup_d = BeautifulSoup(d_text, 'html.parser')
                    description = soup_d.get_text().strip()
                
                print(f"  Processing: {title[:50]}...")
                
                # Extract full text
                full_text = self._extract_text(link)
                
                final_content = full_text if full_text else description
                
                if final_content:
                    news_items.append({
                        "title": title,
                        "content": final_content, # Real Data or Snippet
                        "is_full_text": bool(full_text),
                        "published_at": pubDate,
                        "url": link,
                        "scraped_at": datetime.now().isoformat()
                    })
                    count += 1
                else:
                    print(f"    -> Skipped (No content found)")
                
            return news_items

        except Exception as e:
            print(f"Error fetching news: {e}")
            return []

    def fetch_reddit_posts(self, company, limit=50):
        """
        Fetches recent reddit posts about the company.
        """
        print(f"\nFetching Reddit posts for '{company}'...")
        url = f"https://www.reddit.com/search.json?q={quote(company)}&sort=new&limit={limit}"
        
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36 Script/1.0"
        }
        
        try:
            response = requests.get(url, headers=headers, timeout=10)
            if response.status_code == 429:
                print("  [Warning] Reddit rate limited (429). Skipping.")
                return []
                
            response.raise_for_status()
            
            data = response.json()
            posts = []
            
            children = data.get('data', {}).get('children', [])
            for child in children:
                post = child.get('data', {})
                # We want "clear datas", so prioritie selftext
                content = post.get('selftext')
                if not content: content = post.get('title') # Fallback to title if image post
                
                posts.append({
                    "platform": "Reddit",
                    "content": content,
                    "author": post.get('author'),
                    "date": datetime.fromtimestamp(post.get('created_utc', 0)).isoformat(),
                    "url": post.get('url')
                })
                
            return posts

        except Exception as e:
            print(f"Error fetching Reddit posts: {e}")
            return []

    def fetch_massive_news(self, company, total_limit=50):
        """
        Fetches news using multiple variations of keywords to build a massive dataset.
        """
        print(f"\n[Massive Mode] Fetching news for '{company}'...")
        
        # Define variations to ensure volume
        queries = [
            f"{company}",
            f"{company} share price",
            f"{company} finance",
            f"{company} business",
            f"{company} sustainability",
            f"{company} projects",
            f"{company} growth"
        ]
        
        all_items = []
        seen_urls = set()
        
        # Distribute limit, but at least 5 per query
        limit_per_query = max(5, int(total_limit / len(queries)) + 2)
        
        for q in queries:
            if len(all_items) >= total_limit: break
            
            # Re-use the existing RSS fetcher
            # Note: fetch_news prints "Fetching news...", we might want to silence it or accept it
            items = self.fetch_news(q, limit=limit_per_query)
            
            for item in items:
                if item['content'] and item['url'] not in seen_urls:
                    # Enrich with query info
                    item['search_query'] = q
                    all_items.append(item)
                    seen_urls.add(item['url'])
        
        print(f"\n  [Massive Mode] Collected {len(all_items)} unique articles/snippets.")
        return all_items[:total_limit]

    def save_data(self, items, folder, filename_prefix):
        if not os.path.exists(folder):
            os.makedirs(folder)
            
        if not items: return

        json_path = os.path.join(folder, f"{filename_prefix}_{datetime.now().strftime('%Y%m%d')}.json")
        with open(json_path, 'w', encoding='utf-8') as f:
            json.dump(items, f, indent=2)
            
        print(f"  Saved {len(items)} items to {json_path}")
