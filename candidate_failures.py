"""Classify failures that belong to one remote candidate operation."""

import requests
import logging
from google.api_core.exceptions import GoogleAPICallError
from httpx import NetworkError, RemoteProtocolError, TimeoutException
from langchain_core.exceptions import OutputParserException
from pydantic import ValidationError
from aiogram.exceptions import (
    TelegramAPIError,
    TelegramForbiddenError,
    TelegramUnauthorizedError,
)

logger = logging.getLogger(__name__)


class CandidateResponseError(Exception):
    """A remote response could not be parsed for this repository."""


def is_temporary_candidate_failure(exc: Exception) -> bool:
    """Return true for a failed remote call or candidate-specific model output.

    Authentication/permission errors and unknown exceptions remain fatal so a
    broken global configuration or programming defect is not hidden.
    """
    if isinstance(exc, GoogleAPICallError):
        code = getattr(exc, "code", None)
        return code not in (401, 403)

    if isinstance(exc, CandidateResponseError):
        return True

    if isinstance(exc, requests.exceptions.HTTPError):
        response = exc.response
        if response is None:
            return False
        status = response.status_code
        if status in (401, 403):
            return status == 403 and (
                response.headers.get("X-RateLimit-Remaining") == "0"
                or response.headers.get("Retry-After") is not None
                or "secondary rate limit" in response.text.lower()
            )
        return status == 429 or status >= 500

    if isinstance(exc, (
        requests.exceptions.Timeout,
        requests.exceptions.ConnectionError,
        requests.exceptions.JSONDecodeError,
        TimeoutError,
        ConnectionError,
        TimeoutException,
        NetworkError,
        RemoteProtocolError,
        ValidationError,
        OutputParserException,
    )):
        return True

    if isinstance(exc, TelegramUnauthorizedError | TelegramForbiddenError):
        return False
    if isinstance(exc, TelegramAPIError):
        return True

    return False


def record_candidate_failure(candidate, stage: str, exc: Exception) -> None:
    candidate.processing_error = f"{stage} temporarily failed ({type(exc).__name__})"
    logger.warning(
        "[%s] candidate remains retryable: %s (%s: %s)",
        stage,
        candidate.full_name,
        type(exc).__name__,
        str(exc)[:240],
    )
