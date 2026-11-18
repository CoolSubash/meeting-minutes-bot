"""Errors that the API turns into clear JSON responses."""


class PipelineError(Exception):
    def __init__(self, message: str, status: int = 502, stage: str | None = None):
        super().__init__(message)
        self.message = message
        self.status = status
        self.stage = stage
