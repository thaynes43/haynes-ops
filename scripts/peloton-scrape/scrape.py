from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service

from urllib.parse import urlparse, parse_qs
import time
import re
import requests
from enum import Enum
import os
import json
import yaml
from dotenv import load_dotenv
import tempfile
import git  # GitPython
from github import Github
import subprocess
import sys

class Activity(Enum):
    ALL = "all"
    STRENGTH = "strength"
    YOGA = "yoga"
    MEDITATION = "meditation"
    CARDIO = "cardio"
    STRETCHING = "stretching"
    CYCLING = "cycling"
    #OUTDOOR = "outdoor" # This is 100% audio
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
    
    @staticmethod
    def parseActivitiesFromEnv(env_var):
        """
        Parse a comma-separated string of activities from the environment.
        Defaults to all activities except 'ALL' if not set.
        """
        if not env_var or not env_var.strip():
            # Default: all except ALL
            return [a for a in Activity if a != Activity.ALL]

        selected = []
        for val in env_var.split(","):
            val = val.strip()
            if not val:
                continue
            matched = None
            # Match by value (case-insensitive)
            for a in Activity:
                if a.value.lower() == val.lower():
                    matched = a
                    break
            # Match by name (case-insensitive, allows ALL_CAPS)
            if not matched:
                try:
                    matched = Activity[val.strip().upper()]
                except KeyError:
                    pass
            if matched:
                selected.append(matched)
            else:
                raise ValueError(f"Invalid activity in PELOTON_ACTIVITY: '{val}'")
        return selected

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

            pattern = r"S(\d+)E(\d+)"
            classFolderMatch = re.search(pattern, root)
            parts = root.split(os.sep)
            if len(parts) < 3 or not classFolderMatch:
                print(f"WARN - SKIPPING {root}")
                continue
                #raise ValueError(f"Path \"{root}\" cannot be used to find an activity!")

            activity_name = parts[-3]
            activity = activity_map.get(activity_name.lower())

            # Fallback for ones that don't match enum
            if not activity:
                if activity_name == "Tread Bootcamp":
                    activity = Activity.BOOTCAMP
                elif activity_name == "Row Bootcamp":
                    activity = Activity.ROW_BOOTCAMP
                elif activity_name == "Bike Bootcamp":
                    activity = Activity.BIKE_BOOTCAMP
                elif "50-50" in root or "Bootcamp: 50" in root:             # HACK need to fix my directory after 50/50 disaster
                    print(f"ERROR - {root}")
                    continue
                else:
                    # DO NOT RUN SCRIPT OR WE WILL MISLAIN THE EPISODE #s
                    raise ValueError(f"Activity name {activity_name} does not map to a known activity.")

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

                    if activity_str == "tread bootcamp":
                        activity_str = "bootcamp"
                    elif activity_str == "bike bootcamp":
                        activity_str = "bike_bootcamp"
                    elif activity_str == "row bootcamp":
                        activity_str = "row_bootcamp"

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
                yaml.dump(subs, f, default_flow_style=False, sort_keys=False, allow_unicode=True, width=4096)
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

class PelotonSession:
    def __init__(self, username, password, docker):
        self.username = username
        self.password = password
        chrome_options = Options()
        chrome_options.add_argument("--headless")  # TOGGLE FOR DEBUGGING

        # Pod options 
        chrome_options.add_argument("--no-sandbox")
        chrome_options.add_argument("--disable-dev-shm-usage")

        if docker:
            chrome_options.binary_location = "/usr/bin/chromium"

        tmp_profile = tempfile.mkdtemp()
        print(f"Using Chrome user-data-dir: {tmp_profile} exists: {os.path.exists(tmp_profile)}")
        chrome_options.add_argument(f'--user-data-dir={tmp_profile}')

        self.printChromeVersions()
        print("Launching Chromium with:", chrome_options.arguments)

        service = None
        if docker:
            service = Service("/usr/bin/chromedriver")
        try:
            if docker:
                self.driver = webdriver.Chrome(service=service, options=chrome_options)
            else:
                self.driver = webdriver.Chrome(options=chrome_options)
        except Exception as e:
            print("Failed to launch Chrome:", repr(e))
            raise

    def openSession(self):
        print("Opening session to members.onepeloton.com...")
        self.driver.get("https://members.onepeloton.com/login")
        time.sleep(10)

        self.driver.find_element(By.NAME, "usernameOrEmail").send_keys(self.username)
        self.driver.find_element(By.NAME, "password").send_keys(self.password)
        self.driver.find_element(By.CSS_SELECTOR, 'button[type="submit"]').click()

        # Wait for login to complete
        print("Sleeping to allow login process to complete")
        time.sleep(15)
        return self.driver

    def closeSession(self):
        self.driver.quit()

    def printChromeVersions(self):
        try:
            chromium_version = subprocess.check_output(["chromium", "--version"], text=True).strip()
        except FileNotFoundError:
            chromium_version = "chromium not found"
        except Exception as e:
            chromium_version = f"chromium error: {e}"

        try:
            chromedriver_version = subprocess.check_output(["chromedriver", "--version"], text=True).strip()
        except FileNotFoundError:
            chromedriver_version = "chromedriver not found"
        except Exception as e:
            chromedriver_version = f"chromedriver error: {e}"

        print(f"Chromium version: {chromium_version}")
        print(f"Chromedriver version: {chromedriver_version}")

class PelotonScraper:
    def __init__(self, session, activity, maxClasses, existingCLasses, seasons, scrolls):
        self.session = session
        self.activity = activity
        self.url = "https://members.onepeloton.com/classes/{}?class_languages=%5B%22en-US%22%5D&sort=original_air_time&desc=true".format(activity.value)
        self.maxClasses = maxClasses
        self.existingCLasses = existingCLasses
        self.seasons = seasons
        self.scrolls = scrolls
        self.results = []

    def scrape(self):
        self.session.driver.get(self.url)
        time.sleep(10)

        # Scroll to the bottom n times to load more links
        # TODO if we need more content we can rework this to keep scrolling until we find enough classes.
        #   This will be useful once we are excluding classes we already have
        SCROLL_PAUSE_TIME = 3  # seconds

        for _ in range(self.scrolls):
            self.session.driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
            time.sleep(SCROLL_PAUSE_TIME)

        # Load the links we just stirred up
        links = self.session.driver.find_elements(By.CSS_SELECTOR, 'a[href*="classId="]')
        print(f"Found {len(links)} classes to parse.")

        index = 0
        skipped = 0
        for link in links:
            if len(self.results) >= self.maxClasses:
                print(f"Found {len(self.results)} >= required {self.maxClasses} {self.activity} classes after searching {index}. Skipped {skipped}.")
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
                skipped = skipped + 1
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

    def output(self):
        """
        Returns:
            dict: Nested dict for merging into subscriptions.yaml
        """
        result_dict = {}
        dupe_dict = {}

        for r in self.results:
            if r["activity"].lower() != self.activity.value.lower() and "bootcamp" not in r["activity"].lower():
                print(f'{r["title"]} had invalid activity: {r["activity"]}')
                continue

            # Compose duration key, e.g., '= Stretching (10 min)'
            duration = r.get("season_number")  # This is actually the minutes (duration)
            activity = r["activity"].title()  # For YAML style
            duration_key = f'= {activity} ({duration} min)'

            # Compose episode title
            episode_title = f'{r["title"]} with {r["instructor"]}'.replace("/", "-")

            # Insert into the nested dict structure
            if duration_key not in result_dict:
                result_dict[duration_key] = {}

            # QnD conflict resolution that will still give us a decent name
            if episode_title in result_dict[duration_key]:
                if episode_title not in dupe_dict:
                    dupe_dict[episode_title] = 1
                updated_title = f"{episode_title} ({dupe_dict[episode_title]})"
                dupe_dict[episode_title] += 1
                episode_title = updated_title

            # Compose episode dict
            ep_dict = {
                "download": r["player_url"],
                "overrides": {
                    "tv_show_directory": f'/media/peloton/{r["activity"].title()}/{r["instructor"]}',
                    "season_number": r["season_number"],
                    "episode_number": r["episode_number"]
                }
            }

            result_dict[duration_key][episode_title] = ep_dict

        for duration_key in result_dict:
            print(f"Extracted {len(result_dict[duration_key])} YAML entries for {duration_key}")

        return result_dict
            
    def extract_duration(self, text):
        """Extracts the duration in minutes as an integer from a string like '45 min ...'"""
        match = re.match(r"^\s*(\d+)\s*min", text, re.IGNORECASE)
        if match:
            return int(match.group(1))
        fallback = re.search(r"\b(\d+)\b", text)
        if fallback:
            return int(fallback.group(1))
        return 0

class EnvManager:
    def __init__(self):
        load_dotenv()
        self.username = ""
        self.password = ""
        self.mediaDir = ""
        self.loadRequiredConfiguration()
        self.subsFile = ""
        self.githubRepo = ""
        self.githubToken = ""
        self.loadConditionalConfiguration()
        self.limit = 25
        self.activities = Activity.ALL
        self.docker = True
        self.scrolls = 10
        self.loadOptionalConfiguration()

        self.dirToCloneRepo = "/tmp/peloton-scrape-repo"
        self.bootstrap()

    """
    MUST define to run
    """
    def loadRequiredConfiguration(self):
        self.username = os.environ.get("PELOTON_USERNAME")
        self.password = os.environ.get("PELOTON_PASSWORD")
        self.mediaDir = os.environ.get("MEDIA_DIR")

        print("Required Configuration:")
        print(f"PELOTON_USERNAME={self.username}, PELOTON_PASSWORD={len(self.password)} chars, MEDIA_DIR={self.mediaDir}")
        if not self.username or not self.password or not self.mediaDir:
            raise ValueError("One or more required environment variables are missing!")
        
    """
    Need either a local SUBS_FILE or a GITHUB_REPO_URL & GITHUB_TOKEN - default subscriptions.yaml is relative to the repository
    """
    def loadConditionalConfiguration(self):
        self.subsFile = os.environ.get("SUBS_FILE", "/tmp/peloton-scrape-repo/kubernetes/main/apps/downloads/ytdl-sub/peloton/config/subscriptions.yaml")
        self.githubRepo = os.getenv("GITHUB_REPO_URL", "")
        self.githubToken = os.getenv("GITHUB_TOKEN", "")

        print("Conditional Configuration:")
        print(f"GITHUB_REPO_URL={self.githubRepo}, SUBS_FILE={self.subsFile}, GITHUB_TOKEN={len(self.githubToken)} chars")

        if self.githubRepo and not self.githubToken:
            raise ValueError("Must configure GITHUB_TOKEN if using GITHUB_REPO_URL!")
        
        if self.githubRepo.startswith("https://"):
            self.githubRepo = self.githubRepo[len("https://"):]
        
    """
    Defaults are typically OK to use for these
    """
    def loadOptionalConfiguration(self):
        self.limit = int(os.getenv("PELOTON_CLASS_LIMIT_PER_ACTIVITY", 25))
        self.activities = ActivityData.parseActivitiesFromEnv(os.getenv("PELOTON_ACTIVITY"))
        self.docker = os.getenv("RUN_IN_CONTAINER", "True").strip().lower() in ("1", "true", "yes")
        self.scrolls = int(os.getenv("PELOTON_PAGE_SCROLLS", 10))
        print("Optional Configuration:")
        print(f"PELOTON_CLASS_LIMIT_PER_ACTIVITY={self.limit} (default 25), PELOTON_ACTIVITY={self.activities} (default all but all)")
        print(f"RUN_IN_CONTAINER={self.docker} (default True), PELOTON_PAGE_SCROLLS={self.scrolls} (default 10)")

    def bootstrap(self):
        if not self.githubRepo:
            # Only needed if we are not running against a local repo
            return

        repo_url_with_token = f"https://{self.githubToken}:x-oauth-basic@{self.githubRepo}"

        if not os.path.exists(self.dirToCloneRepo):
            # No repo, fresh clone
            print(f"cloning {self.githubRepo} in {self.dirToCloneRepo}")
            git.Repo.clone_from(repo_url_with_token, self.dirToCloneRepo)
        else:
            # Assume repo is here, do a pull or crash
            print(f"pulling {self.githubRepo}i n {self.dirToCloneRepo}")
            repo = git.Repo(self.dirToCloneRepo)
            repo.git.checkout('main')
            repo.remotes.origin.pull()

    def finalize(self):
        if not self.githubRepo:
            # Only needed if we are not running against a local repo
            return

        branchName = self.commit_and_push(self.dirToCloneRepo)
        self.create_pull_request(
            github_token=self.githubToken,
            repo_name=self.githubRepo.removeprefix("github.com/"),
            head_branch=branchName,
            base_branch="main"
        )

    def commit_and_push(self, dirToCloneRepo, branch_prefix="peloton-update"):
        repo = git.Repo(dirToCloneRepo)
        origin = repo.remote(name='origin')
        
        # Generate a unique branch name
        timestamp = time.strftime("%Y%m%d%H%M%S")
        new_branch = f"{branch_prefix}-{timestamp}"
        
        # Create and checkout new branch
        repo.git.checkout('-b', new_branch)
        
        # Stage changes (edit this if you want to limit to certain files)
        repo.git.add('--all')

        repo.git.config('--local', 'user.email', 'noreply@haynesnetwork.com')
        repo.git.config('--local', 'user.name', 'Peloton Scraper Bot')

        repo.git.commit('-m', f"Auto-update Peloton subscriptions {timestamp}")
        
        # Push new branch to origin
        origin.push(new_branch)
        print(f"Created branch {new_branch} for repo {dirToCloneRepo}")
        return new_branch
    
    def create_pull_request(
        self,
        github_token,
        repo_name,
        head_branch,
        base_branch="main",
        pr_title=None,
        pr_body=None
    ):
        g = Github(github_token)
        repo = g.get_repo(repo_name)
        
        if pr_title is None:
            pr_title = f"Auto-update Peloton subscriptions {time.strftime("%Y%m%d%H%M%S")}"
        if pr_body is None:
            pr_body = "This PR was created automatically by the Peloton subscriptions update script."

        pr = repo.create_pull(
            title=pr_title,
            body=pr_body,
            head=head_branch,
            base=base_branch
        )
        print(f"PR created: {pr.html_url}")
        return pr

if __name__ == "__main__":
    print("////////////////////////////////////////////////////////")
    print("///                READING CONFIG                    ///")
    print("////////////////////////////////////////////////////////")
    config = EnvManager()
    
    print("////////////////////////////////////////////////////////")
    print("///                TAKING INVENTORY                  ///")
    print("////////////////////////////////////////////////////////")
    fileManager = FileManager(config.mediaDir, config.subsFile)

    existingClasses = fileManager.findExistingClasses()
    
    print("REMOVING EXISTING CLASSES FROM SUBSCRIPTIONS")

    fileManager.removeExistingClasses(existingClasses)
    
    #sys.exit(0) # Exit early just to clean file
    
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
    session = PelotonSession(config.username, config.password, config.docker)
    session.openSession()

    for activity in config.activities:
        print(f"FINDING CLASSES FOR {activity.name}: {activity.value}")
        if activity not in seasons:
            seasons[activity] = ActivityData(activity)

        scraper = PelotonScraper(session, activity, config.limit, existingClasses, seasons[activity], config.scrolls)
        scraper.scrape()
        fileManager.addNewClasses(scraper.output())

    print("////////////////////////////////////////////////////////")
    print("///                 WORK COMPLETE                    ///")
    print("////////////////////////////////////////////////////////")
    session.closeSession()
    config.finalize()