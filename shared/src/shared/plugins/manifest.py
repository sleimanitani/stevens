"""Pydantic schema + parser for ``plugin.yaml`` — v0.11 step 1.

The plugin manifest is the single source of truth for what a power or
Mortal needs and how it wants to run. Hephaestus reads it on install
to forge the runtime artifact; Hades reads it on uninstall to know
what to revoke; the operator reads it via ``demiurge powers show <name>``
or ``demiurge hire show <id>`` to verify nothing has drifted.

Manifest shape (powers):

    name: gmail
    kind: power
    display_name: Gmail
    version: 1.0.0
    source: https://github.com/<org>/demiurge-power-gmail
    maintainer: Sol
    modes: [webhook, request-based]
    runtime:
      webhook:
        path: /gmail/push
        port: 8080
        handler: demiurge_power_gmail.adapter:webhook_handler
    capabilities:
      - gmail.send
      - gmail.read
    secrets:
      - name: gmail.oauth_client.id
        prompt: "Google OAuth client ID"
        onboard_via: "demiurge wizard google"
    system_deps:
      apt: []
    bootstrap: demiurge_power_gmail.bootstrap:install

Manifest shape (Mortals):

    name: email_pm
    kind: mortal
    display_name: Email PM
    version: 1.0.0
    capabilities: [gmail.draft, gmail.label]
    powers: [gmail]               # Mortal depends on these powers being installed
    secrets: []
    bootstrap: demiurge_mortal_email_pm.bootstrap:hire

Validation rules (enforced by the Pydantic model):

- ``kind`` is ``"power"`` or ``"mortal"``.
- For powers: ``modes`` is required and non-empty; each mode is one of
  ``webhook | listener | polling | request-based``. ``runtime`` block
  has a key for each declared *reactive* mode (webhook/listener/polling).
  Request-based skips — it just registers capabilities.
- For Mortals: ``modes`` and ``runtime`` are forbidden (Mortals are
  agents, not external integrations).
- ``powers`` is allowed only on Mortals; powers can't depend on other
  powers via this field.
- Secrets follow ``<power>.<key>`` or ``<power>.<account>.<key>`` shape.
"""

from __future__ import annotations

import re
from enum import Enum
from pathlib import Path
from typing import List, Literal, Optional

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator


class ManifestError(Exception):
    """Raised when a plugin manifest fails validation or can't be parsed."""


class Mode(str, Enum):
    """Mechanism a power uses for inbound or outbound traffic."""

    WEBHOOK = "webhook"          # remote pushes to us (HTTPS endpoint)
    LISTENER = "listener"        # we hold a long-lived outbound connection
    POLLING = "polling"          # we hit them on a schedule
    REQUEST_BASED = "request-based"  # agent calls on demand (pure outbound)


REACTIVE_MODES = frozenset({Mode.WEBHOOK, Mode.LISTENER, Mode.POLLING})


PowerKind = Literal["power", "mortal", "beast", "automaton"]
"""Manifest kinds supported in v0.11.

The DEMIURGE.md cosmology defines four Creature kinds + Powers; this
literal is the surface every plugin manifest declares. Originally just
``power | mortal`` in step 1; extended in step 3e.1 with `beast` and
`automaton` once the cosmology lock-in (2026-05-03) made the four-kind
taxonomy load-bearing.
"""


# ----------------------------- runtime sub-blocks --------------------------


class WebhookRuntime(BaseModel):
    """Runtime details for a webhook-mode power."""

    model_config = ConfigDict(extra="forbid")

    path: str = Field(..., description="URL path on the FastAPI mux, e.g. /gmail/push")
    port: int = Field(..., ge=1, le=65535, description="Local port the listener binds to")
    handler: str = Field(
        ..., description="Python entry point handling webhook POSTs (module:attr)"
    )


class ListenerRuntime(BaseModel):
    """Runtime details for a listener-mode power."""

    model_config = ConfigDict(extra="forbid")

    command: str = Field(
        ..., description="Python entry point that runs the listener loop (module:attr)"
    )
    restart: Literal["on-failure", "always", "no"] = "on-failure"


class PollingRuntime(BaseModel):
    """Runtime details for a polling-mode power."""

    model_config = ConfigDict(extra="forbid")

    command: str = Field(
        ..., description="Python entry point invoked once per poll (module:attr)"
    )
    interval: str = Field(
        ...,
        description=(
            "Polling cadence in human-readable form: '30s', '5m', '1h', '1d'. "
            "Hephaestus translates to seconds."
        ),
    )


class RuntimeBlock(BaseModel):
    """Per-mode runtime details. Each declared reactive mode needs a key here."""

    model_config = ConfigDict(extra="forbid")

    webhook: Optional[WebhookRuntime] = None
    listener: Optional[ListenerRuntime] = None
    polling: Optional[PollingRuntime] = None


# ----------------------------- secrets / sysdeps ---------------------------


_SECRET_NAME_RE = re.compile(
    r"^[a-z][a-z0-9_]*"        # power name (e.g. gmail, whatsapp_cloud)
    r"(\.[a-z0-9_-]+)?"        # optional account segment (e.g. .personal)
    r"\.[a-z][a-z0-9_]*$"      # the key segment itself
)


class SecretSpec(BaseModel):
    """A sealed-store secret the plugin expects to exist (or be created on bootstrap)."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., description="Sealed-store key, e.g. gmail.oauth_client.id")
    prompt: str = Field(..., description="Human-readable prompt shown when missing")
    onboard_via: Optional[str] = Field(
        None,
        description=(
            "Optional: command the operator should run to populate this secret "
            "(e.g. 'demiurge wizard google'). Bootstrap hook can route to this."
        ),
    )

    @model_validator(mode="after")
    def _check_name_shape(self) -> "SecretSpec":
        if not _SECRET_NAME_RE.match(self.name):
            raise ManifestError(
                f"secret name {self.name!r} doesn't match "
                f"<power>.<key> or <power>.<account>.<key> shape"
            )
        return self


class SystemDeps(BaseModel):
    """OS-level packages the plugin needs (delegated to the installer Mortal)."""

    model_config = ConfigDict(extra="forbid")

    apt: List[str] = Field(default_factory=list)
    brew: List[str] = Field(default_factory=list)


# ----------------------------- top-level manifest --------------------------


_NAME_RE = re.compile(r"^[a-z][a-z0-9_-]*$")


class Manifest(BaseModel):
    """The full ``plugin.yaml`` schema."""

    model_config = ConfigDict(extra="forbid")

    # Identity
    name: str = Field(..., description="Plugin name, lowercase + dashes/underscores")
    kind: PowerKind = Field(..., description="'power' or 'mortal'")
    display_name: str
    version: str = Field(..., description="Semver string (validated structurally only)")
    source: Optional[str] = Field(None, description="Source repo URL")
    maintainer: Optional[str] = None

    # Power-only
    modes: Optional[List[Mode]] = None
    runtime: Optional[RuntimeBlock] = None

    # Common
    capabilities: List[str] = Field(default_factory=list)
    powers: Optional[List[str]] = None  # Mortal-only — declared dependencies
    secrets: List[SecretSpec] = Field(default_factory=list)
    system_deps: SystemDeps = Field(default_factory=SystemDeps)
    bootstrap: Optional[str] = Field(
        None,
        description=(
            "Python entry point invoked on install / hire (module:attr). "
            "Required for powers; optional for Mortals (defaults to a no-op)."
        ),
    )

    # ---------- validation ----------

    @model_validator(mode="after")
    def _validate_name(self) -> "Manifest":
        if not _NAME_RE.match(self.name):
            raise ManifestError(
                f"name {self.name!r} must be lowercase + start with a letter, "
                f"only contain a-z 0-9 _ -"
            )
        return self

    @model_validator(mode="after")
    def _validate_modes_for_kind(self) -> "Manifest":
        if self.kind == "power":
            if not self.modes:
                raise ManifestError("power: 'modes' is required and non-empty")
            if len(set(self.modes)) != len(self.modes):
                raise ManifestError(f"power: duplicate modes in {self.modes}")
        else:  # mortal | beast | automaton — Creatures, not Powers
            if self.modes is not None:
                raise ManifestError(
                    f"{self.kind}: 'modes' must not be set (Creatures are "
                    f"forged on demand, not external integrations)"
                )
            if self.runtime is not None:
                raise ManifestError(
                    f"{self.kind}: 'runtime' must not be set (use 'bootstrap' instead)"
                )
        return self

    @model_validator(mode="after")
    def _validate_runtime_matches_modes(self) -> "Manifest":
        if self.kind != "power":
            return self
        declared = set(self.modes or [])
        reactive_declared = declared & REACTIVE_MODES
        runtime = self.runtime or RuntimeBlock()

        present = {
            Mode.WEBHOOK if runtime.webhook is not None else None,
            Mode.LISTENER if runtime.listener is not None else None,
            Mode.POLLING if runtime.polling is not None else None,
        } - {None}

        missing = reactive_declared - present
        if missing:
            names = ", ".join(sorted(m.value for m in missing))
            raise ManifestError(
                f"power: declared mode(s) {names} but no matching 'runtime' block"
            )

        extra = present - reactive_declared
        if extra:
            names = ", ".join(sorted(m.value for m in extra))
            raise ManifestError(
                f"power: 'runtime' has block(s) for {names} but those modes "
                f"aren't declared in 'modes'"
            )

        # Pure request-based (no reactive mode) → runtime block must be empty/missing
        if Mode.REQUEST_BASED in declared and not reactive_declared:
            if present:
                raise ManifestError(
                    "power: request-based-only powers must not have a 'runtime' block"
                )
        return self

    @model_validator(mode="after")
    def _validate_powers_field(self) -> "Manifest":
        # `powers` declares "this Creature depends on these Powers being
        # installed." Mortals naturally use it; Beasts may use it (e.g. an
        # image_gen Beast that calls an upstream API via web.fetch);
        # Automatons rarely need it but we allow it. Powers themselves
        # cannot use it — Powers don't depend on other Powers via this field.
        if self.kind == "power" and self.powers is not None:
            raise ManifestError(
                "power: 'powers' field is Creature-only (powers can't "
                "declare power deps via this field)"
            )
        return self

    @model_validator(mode="after")
    def _validate_capabilities_shape(self) -> "Manifest":
        # We don't validate against the live capability registry here — that's
        # a Hephaestus-time check (it has Enkidu's registry handy). Just
        # syntactically: capability names look like "<namespace>.<verb>" with
        # optional dot-separated parts.
        for c in self.capabilities:
            if not re.match(r"^[a-z][a-z0-9_]*(\.[a-z0-9_]+)+$", c):
                raise ManifestError(
                    f"capability {c!r} doesn't match <namespace>.<verb> shape"
                )
        return self

    @model_validator(mode="after")
    def _validate_bootstrap_required_for_powers(self) -> "Manifest":
        if self.kind == "power" and self.bootstrap is None:
            raise ManifestError("power: 'bootstrap' entry point is required")
        return self


# ----------------------------- loaders -------------------------------------


def load_manifest_from_text(text: str) -> Manifest:
    """Parse a YAML string into a ``Manifest``.

    Raises ``ManifestError`` on YAML parse failure or schema validation failure.
    """
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as e:
        raise ManifestError(f"YAML parse failed: {e}") from e

    if not isinstance(data, dict):
        raise ManifestError(
            f"manifest root must be a mapping, got {type(data).__name__}"
        )

    try:
        return Manifest.model_validate(data)
    except ValidationError as e:
        # Pydantic's error is structured but verbose; surface a concise version
        # while preserving the original via __cause__.
        raise ManifestError(_format_pydantic_error(e)) from e


def load_manifest_from_yaml(path: Path | str) -> Manifest:
    """Read and parse ``path`` as a plugin manifest."""
    p = Path(path)
    try:
        text = p.read_text()
    except FileNotFoundError as e:
        raise ManifestError(f"manifest file not found at {p}") from e
    return load_manifest_from_text(text)


def _format_pydantic_error(e: ValidationError) -> str:
    """Render a ValidationError as one short line per failed field."""
    lines = []
    for err in e.errors():
        loc = ".".join(str(x) for x in err["loc"]) or "<root>"
        lines.append(f"{loc}: {err['msg']}")
    return "; ".join(lines)
