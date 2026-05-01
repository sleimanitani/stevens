"""Step types for Charon recipes — the recipe DSL."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Union


@dataclass(frozen=True)
class Nav:
    url: str


@dataclass(frozen=True)
class WaitFor:
    selector: str
    timeout_s: float = 30.0


@dataclass(frozen=True)
class Fill:
    selector: str
    value: str


@dataclass(frozen=True)
class Click:
    selector: str


@dataclass(frozen=True)
class OperatorAction:
    """Pause and tell the operator to do something in the visible browser."""

    message: str


@dataclass(frozen=True)
class Extract:
    """Pull the text content (or value) of ``selector`` into named ``into_slot``."""

    selector: str
    into_slot: str
    use_value: bool = False    # True = read .input_value(); False = .inner_text()


@dataclass(frozen=True)
class StoreSecret:
    """Write the value in ``slot`` to the sealed store under ``secret_name``."""

    slot: str
    secret_name: str
    metadata_kind: str = "charon_extracted"


Step = Union[Nav, WaitFor, Fill, Click, OperatorAction, Extract, StoreSecret]
