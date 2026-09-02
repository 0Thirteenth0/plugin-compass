class StateError(ValueError):
    """Durable controller state cannot be trusted or advanced safely."""


__all__ = ["StateError"]
