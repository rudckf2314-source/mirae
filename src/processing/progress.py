from dataclasses import dataclass, field
from typing import Any, Callable, Protocol


@dataclass
class ProgressEvent:
    step: str
    message: str
    status: str = "completed"
    data: dict[str, Any] = field(default_factory=dict)


ProgressCallback = Callable[[ProgressEvent], None]


class SupportsProgress(Protocol):
    def __call__(self, event: ProgressEvent) -> None: ...


def emit(callback: ProgressCallback | None, step: str, message: str, status: str = "completed", **data: Any) -> None:
    if callback is None:
        return
    callback(ProgressEvent(step=step, message=message, status=status, data=data))
