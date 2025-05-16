from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options
from urllib.parse import urlparse, parse_qs
import time

chrome_options = Options()
# chrome_options.add_argument("--headless")  # Use visible browser for login, headless for automation
driver = webdriver.Chrome(options=chrome_options)

driver.get("https://members.onepeloton.com/classes/cycling?class_languages=%5B%22en-US%22%5D&sort=original_air_time&desc=true")
input("Log in, let the page fully load, then press Enter here...")

# Wait for all thumbnails to load (adjust sleep if needed)
time.sleep(3)

links = driver.find_elements(By.CSS_SELECTOR, 'a[href*="classId="]')

results = []

for idx, link in enumerate(links[:10]):  # Adjust count as needed
    href = link.get_attribute("href")
    parsed = urlparse(href)
    qs = parse_qs(parsed.query)
    class_id = qs.get("classId", [""])[0]
    if not class_id:
        continue

    # Compose player URL
    player_url = f"https://members.onepeloton.com/classes/player/{class_id}"

    # Get metadata from inside the link
    try:
        title = link.find_element(By.CSS_SELECTOR, '[data-test-id="videoCellTitle"]').text
    except Exception:
        title = "Unknown"
    try:
        instructor = link.find_element(By.CSS_SELECTOR, '[data-test-id="videoCellSubtitle"]').text
    except Exception:
        instructor = "Unknown"

    results.append({
        "title": title,
        "instructor": instructor,
        "player_url": player_url,
        "season_number": 1,  # Customize if needed
        "episode_number": idx + 1,
    })

driver.quit()

# Output template
for r in results:
    print(f'''"{r["title"]} w/ {r["instructor"]}":
  download: "{r["player_url"]}"
  overrides:
    tv_show_directory: "/media/peloton/Cycling/{r["instructor"]}"
    season_number: {r["season_number"]}
    episode_number: {r["episode_number"]}
''')
