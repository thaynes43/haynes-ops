from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options
from urllib.parse import urlparse, parse_qs
import time
import re
import requests
from enum import Enum
import os
import json
import yaml

class Activity(Enum):
    ALL = "all"
    STRENGTH = "strength"
    YOGA = "yoga"
    MEDITATION = "meditation"
    CARDIO = "cardio"
    STRETCHING = "stretching"
    CYCLING = "cycling"
    OUTDOOR = "outdoor"
    RUNNING = "running"
    WALKING = "walking"
    BOOTCAMP = "bootcamp"
    BIKE_BOOTCAMP = "bike_bootcamp"
    ROWING = "rowing"
    ROW_BOOTCAMP = "row_bootcamp"

class ActivityData:
    def __init__(self, activity): 
        self.activity = activity
        self.maxEpisode = {}

    def update(self, season, episode):
        if season not in self.maxEpisode or episode > self.maxEpisode[season]:
            self.maxEpisode[season] = episode

    def print(self):
        print(f"Activity: {self.activity.name} ({self.activity.value})")
        for season in sorted(self.maxEpisode):
            print(f"  Season {season}: last episode {self.maxEpisode[season]}")

    @staticmethod
    def mergeCollections(map1, map2):
        """Merge two dicts of ActivityData, keeping the largest episode per season."""
        merged = {}

        all_activities = set(map1.keys()) | set(map2.keys())

        for activity in all_activities:
            merged_data = ActivityData(activity)
            # Collect all unique seasons
            seasons = set()
            if activity in map1:
                seasons.update(map1[activity].maxEpisode.keys())
            if activity in map2:
                seasons.update(map2[activity].maxEpisode.keys())

            for season in seasons:
                ep1 = map1[activity].maxEpisode.get(season, 0) if activity in map1 else 0
                ep2 = map2[activity].maxEpisode.get(season, 0) if activity in map2 else 0
                merged_data.maxEpisode[season] = max(ep1, ep2)

            merged[activity] = merged_data

        return merged
    
class FileManager:
    def __init__(self, mediaDir, subsFile):
        self.mediaDir = mediaDir
        self.subsFile = subsFile

    def findExistingClasses(self):
        ids = set()

        for subdir, _, files in os.walk(self.mediaDir):
            for file in files:
                if file.endswith(".info.json"):
                    path = os.path.join(subdir, file)
                    try:
                        with open(path, "r") as f:
                            data = json.load(f)
                            if "id" in data:
                                ids.add(data["id"])
                    except Exception as e:
                        print(f"Error reading {path}: {e}")

        print(f"Found {len(ids)} existing classes.")
        return ids
    
    def findSubscriptionClasses(self):
        ids = set()
        url_pattern = re.compile(r"https://members\.onepeloton\.com/classes/player/([a-f0-9]+)")

        with open(self.subsFile, "r") as f:
            subs = yaml.safe_load(f)

        for cat_key, cat_val in subs.items():
            if cat_key.startswith('__'):
                continue
            if not isinstance(cat_val, dict):
                continue

            for duration_key, duration_val in cat_val.items():
                if not isinstance(duration_val, dict):
                    continue

                for ep_title, ep_val in duration_val.items():
                    if not isinstance(ep_val, dict):
                        continue
                    url = ep_val.get("download", "")
                    match = url_pattern.match(url)
                    if match:
                        ids.add(match.group(1))

        print(f"Found {len(ids)} subscribed classes.")         
        return ids

    def findMaxEpisodePerActivityFromDisk(self):
        # Map activity string (lowercase) to enum
        activity_map = {a.value.lower(): a for a in Activity}
        results = {}

        for root, dirs, files in os.walk(self.mediaDir):
            if dirs:
                continue  # Only process leaf directories

            parts = root.split(os.sep)
            if len(parts) < 4:
                continue

            activity_name = parts[-3]
            activity = activity_map.get(activity_name.lower())
            if not activity:
                continue

            if activity not in results:
                results[activity] = ActivityData(activity)

            leaf = parts[-1]
            m = re.match(r"S(\d+)E(\d+)", leaf)
            if m:
                season = int(m.group(1))
                episode = int(m.group(2))
                results[activity].update(season, episode)

        return results
    
    def findMaxEpisodePerActivityFromSubscriptions(self):
        activity_map = {}  # Activity enum -> ActivityData

        with open(self.subsFile, "r") as f:
            subs = yaml.safe_load(f)

        # Iterate over top-level keys (skip keys starting with '__')
        for cat_key, cat_val in subs.items():
            if cat_key.startswith('__'):
                continue
            if not isinstance(cat_val, dict):
                continue

            # Iterate over duration keys (= Cycling (20 min)), then episodes
            for duration_key, duration_val in cat_val.items():
                if not isinstance(duration_val, dict):
                    continue

                for ep_title, ep_val in duration_val.items():
                    # Get activity, season, episode from overrides if present
                    if not isinstance(ep_val, dict):
                        continue
                    overrides = ep_val.get("overrides", {})
                    tv_show_directory = overrides.get("tv_show_directory", "")
                    season = overrides.get("season_number", None)
                    episode = overrides.get("episode_number", None)

                    # Extract activity from tv_show_directory if not directly present
                    # Example: "/media/peloton/Cycling/Hannah Corbin"
                    activity_str = None
                    if tv_show_directory:
                        parts = tv_show_directory.split("/")
                        if len(parts) >= 4:
                            activity_str = parts[3].lower()

                    # Only proceed if we actually found an activity string
                    if not activity_str:
                        print(f"Error extracting activity from overrides: {ep_val}")
                        continue

                    # Map string to Activity enum (skip if not recognized)
                    try:
                        activity_enum = Activity(activity_str)
                    except Exception as e:
                        print(f"Error extracting activity enum from string {activity_str} for dir {tv_show_directory}: {e}")
                        continue

                    # Skip if season or episode missing
                    if season is None or episode is None:
                        continue

                    # Insert/update activity data
                    if activity_enum not in activity_map:
                        activity_map[activity_enum] = ActivityData(activity_enum)
                    activity_map[activity_enum].update(int(season), int(episode))

        return activity_map


    def removeExistingClasses(self, existingClasses):
        with open(self.subsFile, "r") as f:
            subs = yaml.safe_load(f)

        changed = False

        # We expect 'Plex TV Show by Date' as a top-level key (adjust if needed)
        shows = subs.get("Plex TV Show by Date", {})
        for group in list(shows):  # e.g., "= Cycling (5 min)"
            group_dict = shows[group]
            for title in list(group_dict):  # e.g., "Cool Down Ride (5 min)"
                item = group_dict[title]
                url = item.get("download", "")
                # Extract class ID from URL
                m = re.search(r'/classes/player/([a-f0-9]+)', url)
                if m:
                    class_id = m.group(1)
                    if class_id in existingClasses:
                        print(f"Removing already-downloaded class: {title} ({class_id})")
                        del group_dict[title]
                        changed = True
            # Remove group if empty
            if not group_dict:
                print(f"Removing empty group: {group}")
                del shows[group]

        if changed:
            with open(self.subsFile, "w") as f:
                yaml.dump(subs, f, default_flow_style=False, sort_keys=False, width=4096)
            print(f"Updated {self.subsFile} with already-downloaded classes removed.")
        else:
            print("No changes made to subscriptions.")

        return changed


    def addNewClasses(self, classes):
        with open(self.subsFile, "r") as f:
            subs = yaml.safe_load(f)

        for header, episodes in classes.items():
            if header not in subs["Plex TV Show by Date"]:
                subs["Plex TV Show by Date"][header] = {}
            # Merge episodes
            for ep_title, ep_val in episodes.items():
                subs["Plex TV Show by Date"][header][ep_title] = ep_val

        with open(self.subsFile, "w") as f:
            yaml.dump(subs, f, sort_keys=False, allow_unicode=True, default_flow_style=False, indent=2, width=4096)

class PelotonScraper:
    def __init__(self, activity, maxClasses, existingCLasses, seasons):
        self.activity = activity
        self.url = "https://members.onepeloton.com/classes/{}?class_languages=%5B%22en-US%22%5D&sort=original_air_time&desc=true".format(activity.value)
        self.maxClasses = maxClasses
        self.existingCLasses = existingCLasses
        self.seasons = seasons
        self.results = []

    def scrape(self):
        chrome_options = Options()
        # chrome_options.add_argument("--headless")  # Use visible browser for login, headless for automation
        driver = webdriver.Chrome(options=chrome_options)

        driver.get("https://members.onepeloton.com/login")
        time.sleep(5)

        driver.find_element(By.NAME, "usernameOrEmail").send_keys("REDACTED@gmail.com")
        driver.find_element(By.NAME, "password").send_keys("REDACTED!")
        driver.find_element(By.CSS_SELECTOR, 'button[type="submit"]').click()

        # Wait for login to complete
        time.sleep(5)

        driver.get(self.url)
        #input("Log in, let the page fully load, then press Enter here...")

        # Wait for all thumbnails to load (adjust sleep if needed)
        time.sleep(5)

        # Scroll to the bottom n times to load more links
        # TODO if we need more content we can rework this to keep scrolling until we find enough classes.
        #   This will be useful once we are excluding classes we already have
        SCROLL_PAUSE_TIME = 2  # seconds

        for _ in range(5):
            driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
            time.sleep(SCROLL_PAUSE_TIME)

        # Load the links we just stirred up
        links = driver.find_elements(By.CSS_SELECTOR, 'a[href*="classId="]')
        print(f"Found {len(links)} classes to parse.")

        index = 0
        for link in links:
            if len(self.results) >= self.maxClasses:
                print(f"Found required {self.maxClasses} classes after searching {index}.")
                break

            index = index + 1
            href = link.get_attribute("href")
            parsed = urlparse(href)
            qs = parse_qs(parsed.query)
            class_id = qs.get("classId", [""])[0]
            if not class_id:
                print(f"Could not extract class_id from link: {href}")
                continue

            if class_id in self.existingCLasses:
                print(f"Skipping class {class_id} since it was found either on the filesystem or in a remaining subscription.")
                continue

            # Compose player URL
            player_url = f"https://members.onepeloton.com/classes/player/{class_id}"

            # Get metadata from inside the link
            try:
                title = link.find_element(By.CSS_SELECTOR, '[data-test-id="videoCellTitle"]').text
            except Exception as e:
                print(f"Error extracting title: {e}")
                title = "Unknown"
            try:
                season = self.extract_duration(title)
            except Exception as e:
                print(f"Error extracting season: {e}")
                season = 0
            try:
                instructor_activity  = link.find_element(By.CSS_SELECTOR, '[data-test-id="videoCellSubtitle"]').text
                parts = instructor_activity.split('·')
                instructor = parts[0].strip().title()
                activity = parts[1].strip().title()
            except Exception as e:
                print(f"Error extracting instructor & activity: {e}")
                instructor = "Unknown"
                activity = "Unknown"

            if season not in self.seasons.maxEpisode:
                self.seasons.maxEpisode[season] = 0
            self.seasons.maxEpisode[season] += 1

            self.results.append({
                "title": title,
                "instructor": instructor,
                "activity": activity,
                "player_url": player_url,
                "season_number": season,
                "episode_number": self.seasons.maxEpisode[season],
            })

        driver.quit()

    def output(self):
        """
        Returns:
            dict: Nested dict for merging into subscriptions.yaml
        """
        result_dict = {}

        for r in self.results:
            if r["activity"].lower() != self.activity.value.lower():
                print(f'{r["title"]} had invalid activity: {r["activity"]}')
                continue

            # Compose duration key, e.g., '= Stretching (10 min)'
            duration = r.get("season_number")  # This is actually the minutes (duration)
            activity = r["activity"].capitalize()  # For YAML style
            duration_key = f'= {activity} ({duration} min)'

            # Compose episode title
            episode_title = f'{r["title"]} with {r["instructor"]}'

            # Compose episode dict
            ep_dict = {
                "download": r["player_url"],
                "overrides": {
                    "tv_show_directory": f'/media/peloton/{r["activity"].capitalize()}/{r["instructor"]}',
                    "season_number": r["season_number"],
                    "episode_number": r["episode_number"]
                }
            }

            # Insert into the nested dict structure
            if duration_key not in result_dict:
                result_dict[duration_key] = {}
            result_dict[duration_key][episode_title] = ep_dict

        return result_dict
            
    def extract_duration(self, text):
        """Extracts the duration in minutes as an integer from a string like '45 min ...'"""
        match = re.match(r"^\s*(\d+)\s*min", text, re.IGNORECASE)
        if match:
            return int(match.group(1))
        return None

if __name__ == "__main__":
    fileManager = FileManager("/mnt/cephfs-hdd/data/media/peloton", "/home/thaynes/workspace/haynes-ops/kubernetes/apps/downloads/ytdl-sub/peloton/config/subscriptions.yaml")
    
    print("////////////////////////////////////////////////////////")
    print("///                TAKING INVENTORY                  ///")
    print("////////////////////////////////////////////////////////")

    print("FINDING EXISTING CLASSES FROM FILE SYSTEM")

    existingClasses = fileManager.findExistingClasses()
    
    print("REMOVING EXISTING CLASSES FROM SUBSCRIPTIONS")

    fileManager.removeExistingClasses(existingClasses)

    print("EXTRACTING SEASONS & EPISODES FROM EXISTING CLASSES")

    seasonsFromDisk = fileManager.findMaxEpisodePerActivityFromDisk()

    print("EXTRACTING SEASONS & EPISODES FROM REMAINING SUBSCRIPTIONS")

    seasonsFromSubs = fileManager.findMaxEpisodePerActivityFromSubscriptions()

    print("MERGING DATA FROM DISK AND SUBSCRIPTIONS")

    seasons = ActivityData.mergeCollections(seasonsFromDisk, seasonsFromSubs)
    for activity in seasons:
        seasons[activity].print()

    subscribedClasses = fileManager.findSubscriptionClasses()
    for id in subscribedClasses:
        existingClasses.add(id)

    print("////////////////////////////////////////////////////////")
    print("///              FINDING NEW CLASSES                 ///")
    print("////////////////////////////////////////////////////////")

    scraper = PelotonScraper(Activity.CYCLING, 30, existingClasses, seasons[Activity.CYCLING])
    scraper.scrape()
    
    fileManager.addNewClasses(scraper.output())