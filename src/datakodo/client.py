"""Main user-facing Client class."""


class Client:
    """Entry point for all data requests.

    Instantiate with a provider name and optional config overrides.
    """

    def __init__(self, provider: str, **kwargs) -> None:
        self._provider = provider
        self._config = kwargs

    def __repr__(self) -> str:
        return f"Client(provider={self._provider!r})"
