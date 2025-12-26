"""
Selenium WebDriver utilities for safe navigation and element handling.
"""

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.options import Options
from selenium.common.exceptions import (
    StaleElementReferenceException,
    TimeoutException,
    WebDriverException,
)
import time
import random
import urllib.parse
import socket

try:
    from fake_useragent import UserAgent
    ua = UserAgent()
except ImportError:
    ua = None

from ..config import SCRAPER_SETTINGS

# List of free proxies (you can update this list or fetch from a proxy API)
# Format: "ip:port" or "http://ip:port"
FREE_PROXIES = [
    # Add proxies here if you have them, e.g.:
    # "http://123.456.789.0:8080",
    "http://195.158.8.123:3128", 
    "http://35.197.89.213:80",
    "http://104.197.218.238:8080"
    
]


def get_random_user_agent():
    """Get a random DESKTOP user agent string (not mobile - mobile sites have different HTML)."""
    # Always use desktop user agents - mobile versions have different page structure
    desktop_agents = [
        # Chrome on Mac
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
        # Chrome on Windows
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
        # Safari on Mac
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Safari/605.1.15",
        # Firefox on Windows
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:122.0) Gecko/20100101 Firefox/122.0",
        # Firefox on Mac
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:121.0) Gecko/20100101 Firefox/121.0",
        # Edge on Windows
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 Edg/120.0.0.0",
        # Chrome on Linux
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    ]
    return random.choice(desktop_agents)


def get_random_proxy():
    """Get a random proxy from the list, if available."""
    if FREE_PROXIES:
        return random.choice(FREE_PROXIES)
    return None


def setup_driver(headless=True, use_proxy=False, rotate_user_agent=True, disable_images=True):
    """
    Set up and return a configured Chrome driver.

    Args:
        headless: Run in headless mode (default: True - OPTIMIZED FOR SPEED)
        use_proxy: Use a random proxy from FREE_PROXIES list (default: False)
        rotate_user_agent: Use a random user agent (default: True)
        disable_images: Disable images and CSS for faster loading (default: True)

    Returns:
        webdriver.Chrome: Configured Chrome driver
    """
    chrome_options = Options()
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--window-size=1920,1080")
    chrome_options.add_argument("--disable-blink-features=AutomationControlled")

    # OPTIMIZATION: Disable images and CSS for faster page loads
    if disable_images:
        prefs = {
            "profile.managed_default_content_settings.images": 2,
            "profile.managed_default_content_settings.stylesheets": 2,
        }
        chrome_options.add_experimental_option("prefs", prefs)

    # Rotate user agent
    if rotate_user_agent:
        user_agent = get_random_user_agent()
        chrome_options.add_argument(f"--user-agent={user_agent}")
        print(f"  Using User-Agent: {user_agent[:50]}...")

    # Use proxy if requested and available
    if use_proxy:
        proxy = get_random_proxy()
        if proxy:
            chrome_options.add_argument(f"--proxy-server={proxy}")
            print(f"  Using Proxy: {proxy}")

    if headless:
        chrome_options.add_argument("--headless=new")
        chrome_options.add_argument("--disable-gpu")

    chrome_options.page_load_strategy = 'eager'  # OPTIMIZED: Changed from 'normal' to 'eager'

    # Exclude automation flags to appear more human-like
    chrome_options.add_experimental_option("excludeSwitches", ["enable-automation"])
    chrome_options.add_experimental_option("useAutomationExtension", False)

    driver = webdriver.Chrome(options=chrome_options)

    # Hide webdriver property
    driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {
        "source": """
            Object.defineProperty(navigator, 'webdriver', {
                get: () => undefined
            })
        """
    })

    # Use timeouts from config (generous values to avoid rate limiting)
    page_load_timeout = SCRAPER_SETTINGS.get("page_load_timeout", 60)
    script_timeout = SCRAPER_SETTINGS.get("script_timeout", 30)

    driver.set_page_load_timeout(page_load_timeout)
    driver.set_script_timeout(script_timeout)

    return driver


def random_delay(min_seconds=1, max_seconds=3):
    """Add a random delay to simulate human behavior."""
    delay = random.uniform(min_seconds, max_seconds)
    time.sleep(delay)
    return delay


def is_valid_url(url):
    """Check if a URL is valid and can be resolved."""
    try:
        parsed_url = urllib.parse.urlparse(url)
        if not parsed_url.scheme or not parsed_url.netloc:
            return False
        socket.gethostbyname(parsed_url.netloc)
        return True
    except (ValueError, socket.gaierror):
        return False


def safe_navigate(driver, url, max_retries=None, add_delay=True):
    """
    Safely navigate to a URL with retries and ensure page is fully loaded.

    Args:
        driver: Selenium WebDriver instance
        url: URL to navigate to
        max_retries: Number of retry attempts
        add_delay: Add random delay before navigation (default: True)

    Returns:
        bool: True if navigation successful, False otherwise
    """
    if max_retries is None:
        max_retries = SCRAPER_SETTINGS["max_retries"]

    if add_delay:
        # Use config delay if available, otherwise default
        delay_setting = SCRAPER_SETTINGS.get("delay_before_navigate", (2, 5))
        if isinstance(delay_setting, tuple):
            random_delay(delay_setting[0], delay_setting[1])
        else:
            random_delay(1, 3)

    for attempt in range(max_retries):
        try:
            driver.get(url)

            # Wait for document ready state
            wait = WebDriverWait(driver, 30)
            wait.until(lambda d: d.execute_script('return document.readyState') == 'complete')

            # Determine page type and wait for appropriate elements
            is_product_page = "/PS" in url or ".htm" not in url

            try:
                if is_product_page:
                    wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "div.pd__wrap")))
                    wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "span.price.pd__price")))
                else:
                    wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "div.container")))
                    wait.until(EC.presence_of_element_located((By.CLASS_NAME, "nf__links")))
                return True
            except TimeoutException:
                # Check if page loaded despite timeout
                if is_product_page:
                    if driver.find_elements(By.CSS_SELECTOR, "div.pd__wrap"):
                        return True
                else:
                    if driver.find_elements(By.CSS_SELECTOR, "div.nf__part"):
                        return True

                if attempt < max_retries - 1:
                    wait_time = 5 * (2 ** attempt)
                    time.sleep(wait_time)
                continue

        except WebDriverException as e:
            print(f"Navigation error (attempt {attempt+1}/{max_retries}): {e}")
            if attempt < max_retries - 1:
                wait_time = 5 * (2 ** attempt)
                time.sleep(wait_time)
            else:
                return False

    return False


def wait_and_find_element(driver, by, value, timeout=None):
    """Wait for an element and handle exceptions."""
    if timeout is None:
        timeout = SCRAPER_SETTINGS["element_timeout"]

    wait = WebDriverWait(driver, timeout)
    try:
        return wait.until(EC.presence_of_element_located((by, value)))
    except (TimeoutException, StaleElementReferenceException):
        return None


def wait_and_find_elements(driver, by, value, timeout=None):
    """Wait for elements and handle exceptions."""
    if timeout is None:
        timeout = SCRAPER_SETTINGS["element_timeout"]

    wait = WebDriverWait(driver, timeout)
    try:
        return wait.until(EC.presence_of_all_elements_located((by, value)))
    except (TimeoutException, StaleElementReferenceException):
        return []


def safe_get_text(element):
    """Safely get text from an element."""
    try:
        return element.text.strip() if element else ""
    except StaleElementReferenceException:
        return ""


def safe_get_attribute(element, attribute):
    """Safely get attribute from an element."""
    try:
        return element.get_attribute(attribute) if element else ""
    except StaleElementReferenceException:
        return ""


def scroll_infinite_container(driver, container_selector, row_selector, max_scrolls=50, scroll_pause=0.5):
    """
    Scroll an infinite scroll container until all content is loaded.

    Args:
        driver: Selenium WebDriver instance
        container_selector: CSS selector for the scrollable container
        row_selector: CSS selector for rows within the container
        max_scrolls: Maximum scroll attempts to prevent infinite loops
        scroll_pause: Seconds to wait between scrolls for content to load

    Returns:
        list: All row elements after fully expanding the container
    """
    try:
        # Find the container
        container = wait_and_find_element(driver, By.CSS_SELECTOR, container_selector, timeout=3)
        if not container:
            return []

        # Get initial row count
        rows = container.find_elements(By.CSS_SELECTOR, row_selector)
        prev_count = len(rows)

        if prev_count == 0:
            return []

        # Scroll until no more content loads
        stable_count = 0
        for scroll_num in range(max_scrolls):
            # Scroll container to bottom using JavaScript
            driver.execute_script("arguments[0].scrollTop = arguments[0].scrollHeight;", container)

            # Wait for content to load
            time.sleep(scroll_pause)

            # Re-find container and rows (avoid stale element issues)
            container = driver.find_element(By.CSS_SELECTOR, container_selector)
            rows = container.find_elements(By.CSS_SELECTOR, row_selector)
            current_count = len(rows)

            # Check if new content was loaded
            if current_count == prev_count:
                stable_count += 1
                # If count is stable for 2 consecutive scrolls, we're done
                if stable_count >= 2:
                    break
            else:
                stable_count = 0
                prev_count = current_count

        # Final re-find to get fresh elements
        container = driver.find_element(By.CSS_SELECTOR, container_selector)
        return container.find_elements(By.CSS_SELECTOR, row_selector)

    except Exception as e:
        # Log but don't fail - return whatever we have
        print(f"Error during infinite scroll: {e}")
        try:
            container = driver.find_element(By.CSS_SELECTOR, container_selector)
            return container.find_elements(By.CSS_SELECTOR, row_selector)
        except Exception:
            return []
