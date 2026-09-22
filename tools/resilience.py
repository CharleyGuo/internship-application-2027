from __future__ import annotations
import time
import inspect
import asyncio
import functools
import logging
from typing import Callable, Any, Type, Tuple, Optional
from playwright.async_api import Page, TimeoutError as PlaywrightTimeoutError

logger = logging.getLogger("resilience")

def retry_with_backoff(
    max_retries: int = 3,
    initial_delay: float = 0.1,
    backoff_factor: float = 2.0,
    exceptions: Tuple[Type[Exception], ...] = (Exception,)
):
    """Decorator supporting both async and sync functions with exponential backoff."""
    def decorator(func: Callable):
        if inspect.iscoroutinefunction(func):
            @functools.wraps(func)
            async def async_wrapper(*args, **kwargs):
                delay = initial_delay
                last_exc = None
                for attempt in range(1, max_retries + 1):
                    try:
                        return await func(*args, **kwargs)
                    except exceptions as e:
                        last_exc = e
                        if attempt == max_retries:
                            break
                        logger.warning(
                            f"[Retry {attempt}/{max_retries}] {func.__name__} failed with {type(e).__name__}: {e}. "
                            f"Retrying in {delay:.2f}s..."
                        )
                        await asyncio.sleep(delay)
                        delay *= backoff_factor
                raise last_exc
            return async_wrapper
        else:
            @functools.wraps(func)
            def sync_wrapper(*args, **kwargs):
                delay = initial_delay
                last_exc = None
                for attempt in range(1, max_retries + 1):
                    try:
                        return func(*args, **kwargs)
                    except exceptions as e:
                        last_exc = e
                        if attempt == max_retries:
                            break
                        logger.warning(
                            f"[Retry {attempt}/{max_retries}] {func.__name__} failed with {type(e).__name__}: {e}. "
                            f"Retrying in {delay:.2f}s..."
                        )
                        time.sleep(delay)
                        delay *= backoff_factor
                raise last_exc
            return sync_wrapper
    return decorator

async def resilient_interact(
    page: Page,
    action: str,
    selector: str,
    value: Optional[str] = None,
    max_retries: int = 3,
    timeout_ms: int = 3000
) -> bool:
    """Executes a Playwright DOM action with automatic retries on detached or unready elements.
    action: 'click', 'fill', 'select'
    """
    delay = 0.2
    for attempt in range(1, max_retries + 1):
        try:
            # Wait for element to be attached and visible
            element = await page.wait_for_selector(selector, state="visible", timeout=timeout_ms)
            if not element:
                raise RuntimeError(f"Element '{selector}' not found.")

            if action == "click":
                await element.click()
            elif action == "fill":
                await element.fill(value or "")
            elif action == "select":
                await element.select_option(value=value)
            return True
        except Exception as e:
            if attempt == max_retries:
                logger.error(f"Action '{action}' on '{selector}' failed after {max_retries} attempts: {e}")
                raise
            await asyncio.sleep(delay)
            delay *= 1.5

    return False
