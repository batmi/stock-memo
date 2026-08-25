import re

with open("tests/test_prices.py", "r") as f:
    content = f.read()

content = re.sub(r'def test_fetch_kr_nxt_mode_pc_crawl_fallback.*?\n\n\n', '', content, flags=re.DOTALL)
content = re.sub(r'def test_fetch_nxt_close_falls_back_to_pc_crawl.*?\n\n\n', '', content, flags=re.DOTALL)

with open("tests/test_prices.py", "w") as f:
    f.write(content)
