class AIModuleError(Exception):
    """Base exception for all ai package failures."""


class ExtractionError(AIModuleError):
    """Raised when meeting data extraction fails or returns invalid data."""


class GenerationError(AIModuleError):
    """Raised when minutes or email generation fails."""
