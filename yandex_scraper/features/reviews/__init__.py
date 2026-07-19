"""Review parser feature modules."""

__all__ = ["build_arg_parser", "main", "run_reviews_parser"]


def __getattr__(name: str):
    if name in __all__:
        from yandex_scraper.features.reviews import runner

        return getattr(runner, name)
    raise AttributeError(name)
