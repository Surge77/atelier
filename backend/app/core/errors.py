"""Application error hierarchy.

Every error carries two messages: ``public_message`` goes to the client and
says only what the caller can act on, while ``detail`` goes to the logs and may
contain paths, provider responses and other things we do not want to leak.
The exception handler in ``main.py`` is the only place that bridges the two.
"""

from __future__ import annotations


class AtelierError(Exception):
    """Base class. Subclasses set the status code and stable error code."""

    status_code = 500
    code = "INTERNAL_ERROR"
    public_message = "An internal error occurred."

    def __init__(self, detail: str = "", public_message: str | None = None) -> None:
        self.detail = detail or self.public_message
        if public_message is not None:
            self.public_message = public_message
        super().__init__(self.detail)


class ConfigurationError(AtelierError):
    """A required setting or credential is missing."""

    status_code = 500
    code = "CONFIGURATION_ERROR"
    public_message = "The server is not configured for this operation."


class InputValidationError(AtelierError):
    status_code = 400
    code = "VALIDATION_ERROR"
    public_message = "The request was invalid."


class UploadRejectedError(AtelierError):
    status_code = 400
    code = "UPLOAD_REJECTED"
    public_message = "The uploaded file was rejected."


class UnsupportedFormatError(AtelierError):
    status_code = 422
    code = "UNSUPPORTED_FORMAT"
    public_message = "That file format is not supported."


class NotFoundError(AtelierError):
    status_code = 404
    code = "NOT_FOUND"
    public_message = "The requested resource does not exist."


class UnauthorizedError(AtelierError):
    status_code = 401
    code = "UNAUTHORIZED"
    public_message = "Missing or invalid API key."


class RateLimitedError(AtelierError):
    status_code = 429
    code = "RATE_LIMITED"
    public_message = "Too many requests. Try again shortly."


class ProviderError(AtelierError):
    """An upstream provider (LLM, search, vector store) failed."""

    status_code = 502
    code = "PROVIDER_ERROR"
    public_message = "An upstream service failed. Try again."


class AgentError(AtelierError):
    """An agent could not produce a usable result."""

    status_code = 500
    code = "AGENT_ERROR"
    public_message = "The assistant could not complete that step."
