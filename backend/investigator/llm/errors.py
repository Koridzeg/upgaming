"""What can go wrong when calling an LLM, and what to do about each.

- RateLimited (429, free-tier quota): retry the same provider only if the wait it asks for is
  short (<= max_wait); otherwise go to the next provider at once.
- ProviderUnavailable (5xx, timeout, connection failure): retry with backoff, then move on.
- InvalidResponse (the body isn't the expected JSON): retry, then move on.
- AuthenticationFailed (401/403, missing or wrong key): never retried - it won't fix itself.
- RequestRejected (other 4xx: context too long, unsupported parameter): never retried - the
  same request gets the same answer.

Every error falls through to the next provider: providers differ in limits, context size and
accepted parameters, so a request one rejects can succeed on another.
"""


class LLMError(Exception):
    retryable = False

    def __init__(self, message: str, provider: str = ""):
        super().__init__(f"[{provider}] {message}" if provider else message)
        self.provider = provider


class RateLimited(LLMError):
    retryable = True

    def __init__(self, message: str, provider: str = "", retry_after: float | None = None):
        super().__init__(message, provider)
        self.retry_after = retry_after


class ProviderUnavailable(LLMError):
    retryable = True


class InvalidResponse(LLMError):
    retryable = True


class AuthenticationFailed(LLMError):
    pass


class RequestRejected(LLMError):
    pass


class AllProvidersFailed(LLMError):
    def __init__(self, errors: list[LLMError]):
        super().__init__("all providers failed: " + "; ".join(str(e) for e in errors))
        self.errors = errors
