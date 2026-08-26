import time
from functools import lru_cache

from openai import APIConnectionError, APIError, APITimeoutError, OpenAI, RateLimitError

from .config import get_api_key

MAX_RETRIES = 2
RETRY_DELAY_SECONDS = 2


@lru_cache(maxsize=1)
def get_client() -> OpenAI:
    return OpenAI(api_key=get_api_key())


def call_with_retry(fn, *args, **kwargs):
    """Calls fn(*args, **kwargs), retrying a couple of times on transient
    rate-limit/API errors before letting the exception propagate."""
    last_error = None
    for attempt in range(MAX_RETRIES + 1):
        try:
            return fn(*args, **kwargs)
        except (RateLimitError, APIError, APIConnectionError, APITimeoutError) as exc:
            last_error = exc
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_DELAY_SECONDS)
    raise last_error
