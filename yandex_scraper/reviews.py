"""Backward-compatible facade for the reviews parser feature."""

from yandex_scraper.features.reviews.date_filter import *
from yandex_scraper.features.reviews.extractors import *
from yandex_scraper.features.reviews.navigation import *
from yandex_scraper.features.reviews.queue import *
from yandex_scraper.features.reviews.records import *
from yandex_scraper.features.reviews.runner import *


if __name__ == "__main__":
    main()
