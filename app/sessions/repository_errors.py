class RecipientSessionError(RuntimeError):
    """Base error for recipient session persistence."""


class RecipientSessionNotFoundError(RecipientSessionError, LookupError):
    """Raised when a session does not exist."""


class RecipientSessionStateError(RecipientSessionError, ValueError):
    """Raised when a transition violates the strict timeline rules."""
