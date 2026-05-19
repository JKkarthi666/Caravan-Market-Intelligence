import time
import random
import pandas as pd

from concurrent.futures import ThreadPoolExecutor, as_completed

from selenium import webdriver
from selenium_stealth import stealth

from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service

from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

from webdriver_manager.chrome import ChromeDriverManager

import gspread
from google.oauth2.service_account import Credentials

from logger import logger
from utils import clean_price
from config import (
    BASE_URL,
    MAX_THREADS,
    OUTPUT_FILE,
    GOOGLE_SHEET_ID,
    GOOGLE_CREDS
)


# =========================================
# DRIVER SETUP
# =========================================

def create_driver():

    options = Options()

    options.add_argument("--start-maximized")

    options.add_argument("--disable-blink-features=AutomationControlled")

    options.add_argument("--no-sandbox")

    options.add_argument("--disable-dev-shm-usage")

    options.add_argument(
        "user-agent=Mozilla/5.0 "
        "(Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 "
        "(KHTML, like Gecko) "
        "Chrome/126.0.0.0 Safari/537.36"
    )

    driver = webdriver.Chrome(
        service=Service(
            ChromeDriverManager().install()
        ),
        options=options
    )

    stealth(
        driver,
        languages=["en-US", "en"],
        vendor="Google Inc.",
        platform="Win32",
        webgl_vendor="Intel Inc.",
        renderer="Intel Iris OpenGL",
        fix_hairline=True,
    )

    driver.set_page_load_timeout(60)

    return driver


# =========================================
# HUMAN SIMULATION
# =========================================

def human_scroll(driver):

    for _ in range(random.randint(5, 10)):

        amount = random.randint(300, 900)

        driver.execute_script(
            f"window.scrollBy(0, {amount});"
        )

        time.sleep(random.uniform(0.5, 1.5))


# =========================================
# SAFE SELECTORS
# =========================================

def safe_text(parent, selector):

    try:

        return parent.find_element(
            By.CSS_SELECTOR,
            selector
        ).text.strip()

    except:

        return ""


def safe_attr(parent, selector, attr):

    try:

        return parent.find_element(
            By.CSS_SELECTOR,
            selector
        ).get_attribute(attr)

    except:

        return ""


# =========================================
# GET PRODUCT URLS
# =========================================

def get_listing_urls():

    driver = create_driver()

    urls = []

    try:

        logger.info("Opening listings page")

        driver.get(BASE_URL)

        WebDriverWait(driver, 20).until(
            EC.presence_of_all_elements_located(
                (By.CSS_SELECTOR, "a.elementor-button")
            )
        )

        human_scroll(driver)

        buttons = driver.find_elements(
            By.CSS_SELECTOR,
            "a.elementor-button"
        )

        for btn in buttons:

            href = btn.get_attribute("href")

            if href and "/product/" in href:

                urls.append(href)

        logger.info(f"Collected {len(urls)} URLs")

    except Exception as e:

        logger.error(f"URL Collection Error: {e}")

    finally:

        driver.quit()

    return list(set(urls))


# =========================================
# SCRAPE PRODUCT DETAILS
# =========================================

def scrape_listing(url, sku):

    driver = create_driver()

    result = {}

    try:

        logger.info(f"Scraping: {url}")

        driver.get(url)

        WebDriverWait(driver, 20).until(
            EC.presence_of_element_located(
                (By.TAG_NAME, "h1")
            )
        )

        human_scroll(driver)

        result["SKU"] = sku

        result["URL"] = url

        result["Title"] = safe_text(
            driver,
            "h1"
        )

        # Prices

        try:

            price_elements = driver.find_elements(
                By.CSS_SELECTOR,
                "div.jet-listing-dynamic-field__content"
            )

            prices = [
                p.text.strip()
                for p in price_elements
                if "$" in p.text
            ]

            result["Raw Price"] = prices[0] if prices else ""

            result["Clean Price"] = clean_price(
                result["Raw Price"]
            )

        except:

            result["Raw Price"] = ""
            result["Clean Price"] = None

        # Description

        result["Description"] = safe_text(
            driver,
            "div.elementor-widget-container"
        )

        # Main Image

        result["Main Image"] = safe_attr(
            driver,
            "img",
            "src"
        )

        # Detail Images

        detail_images = []

        images = driver.find_elements(
            By.CSS_SELECTOR,
            "div.elementor-image-carousel a"
        )

        for img in images:

            href = img.get_attribute("href")

            if href:

                detail_images.append(href)

        result["Detail Images"] = ", ".join(
            detail_images
        )

        # Specifications

        specs = {}

        rows = driver.find_elements(
            By.CSS_SELECTOR,
            "table.jet-table tbody tr"
        )

        for row in rows:

            try:

                key = row.find_element(
                    By.CSS_SELECTOR,
                    "td:nth-child(1)"
                ).text.strip()

                value = row.find_element(
                    By.CSS_SELECTOR,
                    "td:nth-child(2)"
                ).text.strip()

                specs[key] = value

            except:
                continue

        result.update(specs)

        logger.info(f"SUCCESS: {result['Title']}")

    except Exception as e:

        logger.error(f"FAILED: {url} | {e}")

        result["Error"] = str(e)

    finally:

        driver.quit()

    return result


# =========================================
# GOOGLE SHEETS UPLOAD
# =========================================

def upload_to_google_sheets(df):

    try:

        scope = [
            "https://www.googleapis.com/auth/spreadsheets",
            "https://www.googleapis.com/auth/drive"
        ]

        creds = Credentials.from_service_account_file(
            GOOGLE_CREDS,
            scopes=scope
        )

        client = gspread.authorize(creds)

        spreadsheet = client.open_by_key(
            GOOGLE_SHEET_ID
        )

        worksheet = spreadsheet.sheet1

        worksheet.clear()

        worksheet.update(
            [df.columns.values.tolist()] +
            df.values.tolist()
        )

        logger.info(
            "Uploaded to Google Sheets"
        )

    except Exception as e:

        logger.error(
            f"Google Sheets Upload Failed: {e}"
        )


# =========================================
# MAIN PIPELINE
# =========================================

def run_pipeline():

    urls = get_listing_urls()

    logger.info(
        f"Starting scrape for {len(urls)} listings"
    )

    results = []

    with ThreadPoolExecutor(
        max_workers=MAX_THREADS
    ) as executor:

        futures = []

        for idx, url in enumerate(urls):

            sku = f"CFS-{str(idx+1).zfill(5)}"

            futures.append(
                executor.submit(
                    scrape_listing,
                    url,
                    sku
                )
            )

        for future in as_completed(futures):

            try:

                results.append(
                    future.result()
                )

            except Exception as e:

                logger.error(e)

    # Create DataFrame

    df = pd.DataFrame(results)

    # Remove duplicates

    df.drop_duplicates(
        subset=["URL"],
        inplace=True
    )

    # Save Excel

    df.to_excel(
        OUTPUT_FILE,
        index=False
    )

    logger.info(
        f"Excel saved -> {OUTPUT_FILE}"
    )

    # Upload to Google Sheets

    upload_to_google_sheets(df)

    print("\n✅ SCRAPING COMPLETED")
    print(df.head())


# =========================================
# ENTRY
# =========================================

if __name__ == "__main__":

    run_pipeline()
