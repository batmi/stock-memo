import re

with open("tests/test_prices.py", "r") as f:
    content = f.read()

# Remove test_fetch_nxt_pc_crawl_* functions
content = re.sub(r'def test_fetch_nxt_pc_crawl_.*?\n\n\n', '', content, flags=re.DOTALL)
content = re.sub(r'def test_fetch_nxt_pc_crawl_.*?\n\n', '', content, flags=re.DOTALL)

# Remove patch.object(prices, '_fetch_nxt_pc_crawl', ...) lines from tests
content = re.sub(r'patch\.object\(prices,\s*\'_fetch_nxt_pc_crawl\',\s*return_value=[^\)]+\)(,\s*\\|\s*:)\n', ':\n', content)
content = re.sub(r'\s*patch\.object\(prices,\s*\'_fetch_nxt_pc_crawl\',\s*return_value=[^\)]+\):\n', ':\n', content)

# Fix dangling commas from removed patches
content = re.sub(r', \\\n\s*:', ':', content)

with open("tests/test_prices.py", "w") as f:
    f.write(content)
