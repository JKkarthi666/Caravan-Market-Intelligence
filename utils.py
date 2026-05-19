import re


def clean_price(price):

    if not price:
        return None

    cleaned = re.sub(r"[^\d]", "", price)

    return int(cleaned) if cleaned else None