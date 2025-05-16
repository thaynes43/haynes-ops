import time
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options

chrome_options = Options()
chrome_options.add_argument("--headless")  # run headless

driver = webdriver.Chrome(options=chrome_options)
driver.get("https://members.onepeloton.com/classes/cycling?class_languages=%5B%22en-US%22%5D&sort=original_air_time&desc=true")

# Wait for classes to load
time.sleep(5)

# Find all class tiles
tiles = driver.find_elements(By.CSS_SELECTOR, 'div[class^="classCard"] a')

results = []

for idx, tile in enumerate(tiles[:5]):  # Limit to first 5 for demo
    # Open in new tab to avoid losing your place
    link = tile.get_attribute("href")
    driver.execute_script(f"window.open('{link}','_blank');")
    driver.switch_to.window(driver.window_handles[-1])
    time.sleep(4)

    # Click Start (wait for it to appear)
    try:
        start_btn = driver.find_element(By.XPATH, "//button[contains(.,'Start')]")
        start_btn.click()
        time.sleep(2)

        # Grab the player link (the URL now contains /player/<id>)
        player_url = driver.current_url
    except Exception as e:
        player_url = "Not found"

    # Extract metadata (example for title, duration, instructor)
    try:
        title = driver.find_element(By.CSS_SELECTOR, 'h1').text
        meta = driver.find_elements(By.CSS_SELECTOR, '[data-test-id="classDetails-meta"] span')
        duration = meta[0].text if meta else ""
        instructor = driver.find_element(By.CSS_SELECTOR, '[data-test-id="classDetails-instructor"]').text
    except Exception:
        title, duration, instructor = "Unknown", "Unknown", "Unknown"

    results.append({
        "title": title,
        "duration": duration,
        "instructor": instructor,
        "player_url": player_url,
        "season_number": 1,  # you can customize
        "episode_number": idx + 1,
    })

    driver.close()
    driver.switch_to.window(driver.window_handles[0])

driver.quit()

# Fill out template
for r in results:
    print(f'''"{r["title"]} w/ {r["instructor"]} ({r["duration"]})":
  download: "{r["player_url"]}"
  overrides:
    tv_show_directory: "/media/peloton/Cycling/{r["instructor"]}"
    season_number: {r["season_number"]}
    episode_number: {r["episode_number"]}
''')
