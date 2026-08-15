from ragz.core.errors import RagzError


class EmailError(RagzError):
    """Typed error for the email module: misconfigured provider, missing
    secret, or a send failure. Maps to RFC 9457 via the global RagzError
    handler."""

    status_code = 502
    title = "Email delivery error"
