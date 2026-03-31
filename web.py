import re
import time
from selenium import webdriver
from selenium.webdriver import ActionChains
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, WebDriverException

DATE_RE = re.compile(
    r"(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*[,\s]+\d{4}",
    re.IGNORECASE,
)
RATING_RE = re.compile(r"^\d(\.\d)?$")  # "5", "4.5", "5.0"


class FlipkartScraper:
    def __init__(self):
        options = webdriver.ChromeOptions()

        # Headless mode
        options.add_argument("--headless=new")
        options.add_argument("--window-size=1920,1080")

        # Stability flags — prevents tab crashes on repeated scroll operations
        options.add_argument("--disable-notifications")
        options.add_argument("--disable-infobars")
        options.add_argument("--disable-extensions")
        options.add_argument("--disable-gpu")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--disable-crash-reporter")
        options.add_argument("--disable-background-networking")
        options.add_argument("--disable-renderer-backgrounding")
        options.add_argument(
            "user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 Chrome/120 Safari/537.36"
        )

        # Platform-aware driver setup
        import platform, shutil
        if platform.system() == "Linux":
            chromedriver_path = shutil.which("chromedriver") or "/usr/bin/chromedriver"
            chromium_path = (
                shutil.which("chromium-browser")
                or shutil.which("chromium")
                or "/usr/bin/chromium"
            )
            options.binary_location = chromium_path
            self.driver = webdriver.Chrome(service=Service(chromedriver_path), options=options)
        else:
            # Selenium 4.6+ built-in manager auto-detects Chrome and ChromeDriver
            self.driver = webdriver.Chrome(options=options)

        self.wait = WebDriverWait(self.driver, 10)
        self._review_url = None

    # ── internal helpers ──────────────────────────────────────────────────────

    def _visible_review_comments(self, limit=5):
        comments = []
        try:
            body_divs = self.driver.find_elements(
                By.XPATH,
                "//div[@dir='auto' and .//span[contains(@class,'css-1jxf684') "
                "and string-length(normalize-space(text())) > 20]]",
            )
            for div in body_divs[:limit]:
                try:
                    spans = div.find_elements(
                        By.XPATH,
                        ".//span[contains(@class,'css-1jxf684') "
                        "and string-length(normalize-space(text())) > 20]",
                    )
                    if spans:
                        comments.append(spans[0].text.strip())
                except Exception:
                    pass
        except Exception:
            pass
        return comments

    def _extract_reviews(self, limit=10):
        """Scroll to load content then extract up to `limit` reviews from the page."""
        # Re-navigate if tab crashed
        try:
            for _ in range(3):
                self.driver.execute_script("window.scrollBy(0, 800);")
                time.sleep(0.5)
            self.driver.execute_script("window.scrollTo(0, 0);")
            time.sleep(0.5)
        except WebDriverException:
            self.driver.get(self._review_url)
            time.sleep(3)

        data = []
        try:
            body_divs = self.driver.find_elements(
                By.XPATH,
                "//div[@dir='auto' and .//span[contains(@class,'css-1jxf684') "
                "and string-length(normalize-space(text())) > 20]]",
            )
            for div in body_divs[:limit]:
                comment = ""
                try:
                    spans = div.find_elements(
                        By.XPATH,
                        ".//span[contains(@class,'css-1jxf684') "
                        "and string-length(normalize-space(text())) > 20]",
                    )
                    if spans:
                        comment = spans[0].text.strip()
                except Exception:
                    pass

                if not comment:
                    continue

                rating = ""
                date = ""
                try:
                    card = div.find_element(By.XPATH, "ancestor::div[6]")
                    for el in card.find_elements(By.XPATH, ".//*[@dir='auto']"):
                        t = el.text.strip()
                        if not rating and RATING_RE.match(t):
                            rating = t
                        if not date and DATE_RE.search(t) and len(t) < 50:
                            date = t.lstrip(" \u2022\u00b7\u25cf").strip()
                        if rating and date:
                            break
                except Exception:
                    pass

                data.append({"comment": comment, "rating": rating, "date": date})
        except Exception:
            pass

        return data

    def _apply_filter_and_extract(self, *names, limit=10):
        """Navigate fresh to the review page, apply a sort filter, then extract reviews.

        Re-navigating before each filter prevents Chrome memory buildup (tab crashes).
        """
        self.driver.get(self._review_url)
        time.sleep(2)

        before_comments = self._visible_review_comments()
        xpath_options = []
        for name in names:
            xpath_options.extend([
                f"//div[@dir='auto' and normalize-space(text())='{name}']",
                f"//*[self::div or self::span or self::button][normalize-space(text())='{name}']",
                f"//*[self::div or self::span or self::button][contains(normalize-space(text()),'{name}')]",
            ])

        for xpath in xpath_options:
            elements = self.driver.find_elements(By.XPATH, xpath)
            for btn in elements:
                try:
                    self.driver.execute_script(
                        "arguments[0].scrollIntoView({block:'center'});", btn
                    )
                    try:
                        ActionChains(self.driver).move_to_element(btn).pause(0.2).click(btn).perform()
                    except Exception:
                        self.driver.execute_script("arguments[0].click();", btn)

                    for _ in range(8):
                        time.sleep(0.5)
                        after_comments = self._visible_review_comments()
                        if after_comments and after_comments != before_comments:
                            self.driver.execute_script("window.scrollTo(0, 0);")
                            time.sleep(0.5)
                            return self._extract_reviews(limit)
                except Exception:
                    pass

        # Filter not found — return whatever is on the page
        return self._extract_reviews(limit)

    # ── public API ────────────────────────────────────────────────────────────

    def search_product(self, query):
        self.driver.get("https://www.flipkart.com/")
        try:
            close_btn = self.wait.until(
                EC.element_to_be_clickable((By.XPATH, "//button[contains(text(),'✕')]"))
            )
            close_btn.click()
        except TimeoutException:
            pass

        search_box = self.wait.until(EC.presence_of_element_located((By.NAME, "q")))
        search_box.send_keys(query)
        search_box.submit()

    def get_product_details(self):
        first_product = self.wait.until(
            EC.element_to_be_clickable((By.XPATH, "(//a[contains(@href,'/p/')])[1]"))
        )
        first_product.click()

        if len(self.driver.window_handles) > 1:
            self.driver.switch_to.window(self.driver.window_handles[-1])
        time.sleep(2)

        # Scrape product name
        product_name = ""
        for xpath in ["//span[@class='VU-ZEz']", "//h1//span", "//h1"]:
            try:
                product_name = self.driver.find_element(By.XPATH, xpath).text.strip()
                if product_name:
                    break
            except Exception:
                pass

        # Scrape product price
        product_price = ""
        for xpath in [
            "//div[@class='Nx9bqj CxhGGd']",
            "//*[contains(@class,'_30jeq3')]",
            "//*[contains(@class,'Nx9bqj')]",
        ]:
            try:
                product_price = self.driver.find_element(By.XPATH, xpath).text.strip()
                if product_price:
                    break
            except Exception:
                pass

        # Build review page URL
        product_url = self.driver.current_url
        self._review_url = product_url.replace("/p/", "/product-reviews/", 1)
        self.driver.get(self._review_url)
        time.sleep(3)

        # Rating distribution
        rating_data = {}
        try:
            rows = self.driver.find_elements(
                By.XPATH,
                "//div[contains(@class,'r-1awozwy') and .//div[@dir='auto']]",
            )
            for row in rows:
                try:
                    texts = row.text.split("\n")
                    if len(texts) >= 3:
                        rating = texts[0].strip()
                        count = int(texts[-1].strip().replace(",", ""))
                        if rating in ["1", "2", "3", "4", "5"]:
                            rating_data[f"{rating}_star"] = count
                except Exception:
                    pass
        except Exception:
            pass

        # Collect reviews for each sort order (fresh navigation each time)
        most_helpful = self._apply_filter_and_extract("Most Helpful")
        latest = self._apply_filter_and_extract("Newest First", "Latest", "Most Recent")
        positive = self._apply_filter_and_extract("Positive First", "Positive")
        negative = self._apply_filter_and_extract("Negative First", "Negative")

        return {
            "product_name": product_name,
            "product_price": product_price,
            "ratings_distribution": rating_data,
            "reviews": {
                "most_helpful": most_helpful,
                "latest": latest,
                "positive": positive,
                "negative": negative,
            },
        }


if __name__ == "__main__":
    import json

    scraper = FlipkartScraper()
    scraper.search_product("iphone 13")
    data = scraper.get_product_details()
    scraper.driver.quit()

    print(json.dumps(data, ensure_ascii=False, indent=2))
