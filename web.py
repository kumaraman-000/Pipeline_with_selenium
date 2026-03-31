import re
import time
from selenium import webdriver
from selenium.webdriver import ActionChains
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException

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

        # Performance
        options.add_argument("--disable-notifications")
        options.add_argument("--disable-infobars")
        options.add_argument("--disable-extensions")
        options.add_argument("--disable-gpu")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")

        # Real user
        options.add_argument(
            "user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120 Safari/537.36"
        )

        # Create driver
        self.driver = webdriver.Chrome(options=options)

        
        # driver=webdriver.Chrome()
        # self.driver = driver
        # driver.maximize_window()  # Maximize the browser window to ensure all elements are visible


        # Create wait (IMPORTANT)
        self.wait = WebDriverWait(self.driver, 10)

    def _visible_review_comments(self, limit=5):
        comments = []
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
        return comments

    def search_product(self, query):
        self.driver.get("https://www.flipkart.com/")

        # Close login popup
        try:
            close_btn = self.wait.until(
                EC.element_to_be_clickable((By.XPATH, "//button[contains(text(),'✕')]"))
            )
            close_btn.click()
        except TimeoutException:
            pass

        search_box = self.wait.until(
            EC.presence_of_element_located((By.NAME, "q"))
        )
        search_box.send_keys(query)
        search_box.submit()

    def get_product_details(self):

        # Click first product link in search results
        first_product = self.wait.until(
            EC.element_to_be_clickable(
                (By.XPATH, "(//a[contains(@href,'/p/')])[1]")
            )
        )
        first_product.click()

        # Switch to new tab if opened
        if len(self.driver.window_handles) > 1:
            self.driver.switch_to.window(self.driver.window_handles[-1])
        time.sleep(2)

        # Navigate directly to the review page by transforming the product URL
        # Product URL: /product-name/p/ITEM_ID?pid=...
        # Review URL:  /product-name/product-reviews/ITEM_ID?pid=...
        product_url = self.driver.current_url
        review_url = product_url.replace("/p/", "/product-reviews/", 1)
        self.driver.get(review_url)
        time.sleep(3)

        # ⭐ RATING DISTRIBUTION — collected from the review page
        rating_data = {}

        try:
            rows = self.driver.find_elements(
                By.XPATH,
                "//div[contains(@class,'r-1awozwy') and .//div[@dir='auto']]"
            )

            for row in rows:
                try:
                    texts = row.text.split("\n")

                    # Expected format: [rating, ★, count]
                    if len(texts) >= 3:
                        rating = texts[0].strip()
                        count = texts[-1].strip()

                        # Clean count (remove commas)
                        count = int(count.replace(",", ""))

                        if rating in ["1", "2", "3", "4", "5"]:
                            rating_data[f"{rating}_star"] = count

                except Exception as e:
                    print("Row error:", e)

        except Exception as e:
            print("Main error:", e)

        # 🔁 EXTRACT REVIEWS
        def extract_reviews(limit=10):
            # Scroll to load lazy content
            for _ in range(5):
                self.driver.execute_script("window.scrollBy(0, 800);")
                time.sleep(0.6)
            self.driver.execute_script("window.scrollTo(0, 0);")
            time.sleep(0.5)

            data = []

            # Review body text is inside  <span class="css-1jxf684">
            # wrapped in a  <div dir="auto">  — this is stable across Flipkart's new layout
            body_divs = self.driver.find_elements(
                By.XPATH,
                "//div[@dir='auto' and .//span[contains(@class,'css-1jxf684') "
                "and string-length(normalize-space(text())) > 20]]",
            )

            for div in body_divs[:limit]:
                # --- comment ---
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

                # --- walk up 6 levels to reach the review card ---
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

            return data

        # 🔄 APPLY FILTER
        def apply_filter(*names):
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
                        self.driver.execute_script("arguments[0].scrollIntoView({block:'center'});", btn)
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
                                return True
                    except Exception:
                        pass
            return False

        # 💬 GET DATA
        apply_filter("Most Helpful")
        most_helpful = extract_reviews()

        apply_filter("Newest First", "Latest", "Most Recent")
        latest = extract_reviews()

        apply_filter("Positive First", "Positive")
        positive = extract_reviews()

        apply_filter("Negative First", "Negative")
        negative = extract_reviews()

        final_data = {
            "ratings_distribution": rating_data,
            "reviews": {
                "most_helpful": most_helpful,
                "latest": latest,
                "positive": positive,
                "negative": negative
            }
        }

        return final_data


if __name__ == "__main__":
    scraper = FlipkartScraper()

    scraper.search_product("iphone12")
    data = scraper.get_product_details()

    import json
    print("\nFINAL OUTPUT:\n")
    print(json.dumps(data, ensure_ascii=False, indent=2))

    scraper.driver.quit()
