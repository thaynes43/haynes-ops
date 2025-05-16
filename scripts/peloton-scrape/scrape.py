from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options
from urllib.parse import urlparse, parse_qs
import time
import re

def extract_duration(text):
    """Extracts the duration in minutes as an integer from a string like '45 min ...'"""
    match = re.match(r"^\s*(\d+)\s*min", text, re.IGNORECASE)
    if match:
        return int(match.group(1))
    return None

chrome_options = Options()
# chrome_options.add_argument("--headless")  # Use visible browser for login, headless for automation
driver = webdriver.Chrome(options=chrome_options)

#driver.get("https://members.onepeloton.com/classes/cycling?class_languages=%5B%22en-US%22%5D&sort=original_air_time&desc=true")
#driver.get("https://members.onepeloton.com/classes/all")
driver.get("https://members.onepeloton.com/classes/stretching")

input("Log in, let the page fully load, then press Enter here...")

# Wait for all thumbnails to load (adjust sleep if needed)
time.sleep(3)

links = driver.find_elements(By.CSS_SELECTOR, 'a[href*="classId="]')

results = []
episodes = {}

for idx, link in enumerate(links[:30]):  # Adjust count as needed
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
        season = extract_duration(title)
    except Exception:
        season = 0
    try:
        instructor_activity  = link.find_element(By.CSS_SELECTOR, '[data-test-id="videoCellSubtitle"]').text
        parts = instructor_activity.split('·')
        instructor = parts[0].strip().title()
        activity = parts[1].strip().title()
    except Exception:
        instructor = "Unknown"
        activity = "Unknown"

    if season not in episodes:
        episodes[season] = 0
    episodes[season] += 1

    results.append({
        "title": title,
        "instructor": instructor,
        "activity": activity,
        "player_url": player_url,
        "season_number": season,
        "episode_number": episodes[season],
    })

driver.quit()

# Output template
for r in results:
    if {r["activity"]} == 'German':
        continue

    print(f'''"{r["title"]} with {r["instructor"]}":
  download: "{r["player_url"]}"
  overrides:
    tv_show_directory: "/media/peloton/{r["activity"]}/{r["instructor"]}"
    season_number: {r["season_number"]}
    episode_number: {r["episode_number"]}
''')