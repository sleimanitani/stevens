"""Entry-point discovery for installed Demiurge plugins — v0.11 step 2.

Demiurge core scans two ``importlib.metadata`` entry-point groups at
startup:

- ``demiurge.powers`` — external-world integrations (gmail, calendar,
  image-generator, RSS reader, signal-cli daemon, …).
- ``demiurge.mortals`` — task-scoped agents (email_pm, installer,
  trip-planner, …).

A plugin registers itself by adding to its ``pyproject.toml``::

    [project.entry-points."demiurge.powers"]
    gmail = "demiurge_power_gmail:manifest"

The entry point's load target must be either:

1. A ``Manifest`` instance directly (e.g. a module-level constant), or
2. A zero-argument callable that returns a ``Manifest``.

``discover(kind)`` returns one ``InstalledPlugin`` per entry point in
the group, with the loaded manifest + distribution metadata. Anything
that fails to load (broken import, malformed manifest) is collected as
a ``DiscoveryError`` and surfaced to the caller — Demiurge prefers to
keep going with the plugins that *do* work and tell the operator about
the ones that don't, rather than failing startup.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from importlib import metadata as importlib_metadata
from importlib import resources as importlib_resources
from pathlib import Path
from typing import Iterable, Literal, Optional

from .manifest import Manifest, ManifestError, load_manifest_from_yaml

POWERS_GROUP = "demiurge.powers"
MORTALS_GROUP = "demiurge.mortals"

PluginKind = Literal["power", "mortal"]


@dataclass(frozen=True)
class InstalledPlugin:
    """One discovered plugin entry — manifest + distribution metadata."""

    name: str                # entry-point key (e.g. "gmail")
    kind: PluginKind
    manifest: Manifest
    dist_name: str           # the installed package name (e.g. "demiurge-power-gmail")
    dist_version: str
    entry_point_value: str   # raw "module:attr" string — useful in error messages


@dataclass(frozen=True)
class DiscoveryError:
    """One entry point that failed to load. Caller decides what to do."""

    group: str
    name: str                # entry-point key
    dist_name: Optional[str] # may be None if even the dist lookup failed
    entry_point_value: str
    error: str               # short human-readable reason


@dataclass
class DiscoveryResult:
    """The output of ``discover()``: what worked, what didn't."""

    plugins: list[InstalledPlugin] = field(default_factory=list)
    errors: list[DiscoveryError] = field(default_factory=list)

    def names(self) -> list[str]:
        return [p.name for p in self.plugins]


# ----------------------------- group selection ---------------------------


def _group_for_kind(kind: PluginKind) -> str:
    if kind == "power":
        return POWERS_GROUP
    if kind == "mortal":
        return MORTALS_GROUP
    raise ValueError(f"unknown plugin kind: {kind!r}")


def _select_entry_points(group: str) -> Iterable[importlib_metadata.EntryPoint]:
    """Wrapped ``entry_points(group=...)`` — abstraction point for tests."""
    return importlib_metadata.entry_points(group=group)


def _dist_for_entry_point(ep: importlib_metadata.EntryPoint) -> Optional[importlib_metadata.Distribution]:
    """Try to find the distribution that exposes this entry point.

    Returns ``None`` if we can't look it up (older Python, namespace
    weirdness). Discovery still proceeds — we just can't report version
    info for that plugin.
    """
    dist = getattr(ep, "dist", None)
    if dist is not None:
        return dist
    return None


# ----------------------------- the loader --------------------------------


def _load_manifest_from_entry_point(
    ep: importlib_metadata.EntryPoint,
) -> Manifest:
    """Resolve the entry point and coerce its target into a ``Manifest``.

    Accepts:
    - A ``Manifest`` instance.
    - A zero-arg callable that returns a ``Manifest``.

    Anything else raises ``ManifestError`` for caller to wrap.
    """
    target = ep.load()
    if isinstance(target, Manifest):
        return target
    if callable(target):
        result = target()
        if isinstance(result, Manifest):
            return result
        raise ManifestError(
            f"entry point {ep.value!r} callable returned "
            f"{type(result).__name__}, expected Manifest"
        )
    raise ManifestError(
        f"entry point {ep.value!r} resolved to {type(target).__name__}, "
        f"expected Manifest or zero-arg callable returning Manifest"
    )


def _kind_matches(manifest: Manifest, kind: PluginKind) -> bool:
    """Catch the case where a plugin lands in the wrong group.

    A power's manifest must say ``kind: power``, and similarly for Mortals.
    Plugins that lie about which group they belong to are surfaced as
    discovery errors rather than silently misclassified.
    """
    return manifest.kind == kind


# ----------------------------- public API --------------------------------


def discover(kind: PluginKind) -> DiscoveryResult:
    """Scan installed entry points for ``demiurge.<kind>s`` and return all
    plugins, partitioned into successes and errors.

    Every entry point is examined independently; one broken plugin doesn't
    mask the others. Operators see the full picture via
    ``demiurge powers list`` / ``demiurge hire list`` (and ``doctor``
    reports any errors).
    """
    group = _group_for_kind(kind)
    result = DiscoveryResult()

    for ep in _select_entry_points(group):
        dist = _dist_for_entry_point(ep)
        dist_name = dist.metadata["Name"] if dist is not None else None
        dist_version = dist.version if dist is not None else "unknown"

        try:
            manifest = _load_manifest_from_entry_point(ep)
        except ManifestError as e:
            result.errors.append(
                DiscoveryError(
                    group=group,
                    name=ep.name,
                    dist_name=dist_name,
                    entry_point_value=ep.value,
                    error=str(e),
                )
            )
            continue
        except Exception as e:  # noqa: BLE001 — broad on purpose; broken imports etc
            result.errors.append(
                DiscoveryError(
                    group=group,
                    name=ep.name,
                    dist_name=dist_name,
                    entry_point_value=ep.value,
                    error=f"failed to load entry point: {type(e).__name__}: {e}",
                )
            )
            continue

        if not _kind_matches(manifest, kind):
            result.errors.append(
                DiscoveryError(
                    group=group,
                    name=ep.name,
                    dist_name=dist_name,
                    entry_point_value=ep.value,
                    error=(
                        f"manifest declares kind={manifest.kind!r} but is "
                        f"registered under {group!r} (expected kind={kind!r})"
                    ),
                )
            )
            continue

        if manifest.name != ep.name:
            # The entry-point key and the manifest's `name:` field should
            # agree. If they don't, prefer the manifest but warn the operator
            # — it's the kind of drift that causes "why doesn't `demiurge
            # powers install gmail` work after I renamed the entry point"
            # bug reports.
            result.errors.append(
                DiscoveryError(
                    group=group,
                    name=ep.name,
                    dist_name=dist_name,
                    entry_point_value=ep.value,
                    error=(
                        f"entry-point key {ep.name!r} doesn't match manifest "
                        f"name {manifest.name!r}; rename one to agree"
                    ),
                )
            )
            continue

        result.plugins.append(
            InstalledPlugin(
                name=ep.name,
                kind=kind,
                manifest=manifest,
                dist_name=dist_name or "<unknown>",
                dist_version=dist_version,
                entry_point_value=ep.value,
            )
        )

    return result


# ----------------------------- package-name lookup -----------------------


def load_manifest_for_package(package_name: str) -> Manifest:
    """Convenience: load a ``plugin.yaml`` shipped inside ``package_name``.

    Plugin packages that ship a ``plugin.yaml`` data file (instead of, or in
    addition to, the ``manifest`` entry point) can use this from their
    own ``manifest()`` callable::

        # in demiurge_power_gmail/__init__.py
        from shared.plugins.discovery import load_manifest_for_package
        def manifest():
            return load_manifest_for_package("demiurge_power_gmail")

    Looks for ``plugin.yaml`` at the package root via
    ``importlib.resources``. Raises ``ManifestError`` on missing file or
    parse failure (delegated to ``load_manifest_from_yaml``).
    """
    try:
        files = importlib_resources.files(package_name)
    except (ModuleNotFoundError, TypeError) as e:
        raise ManifestError(
            f"package {package_name!r} not importable: {e}"
        ) from e

    candidate = files / "plugin.yaml"
    try:
        is_file = candidate.is_file()
    except Exception:  # noqa: BLE001
        is_file = False

    if not is_file:
        raise ManifestError(
            f"no plugin.yaml found inside {package_name!r}"
        )

    # Materialize to a real path so load_manifest_from_yaml's I/O works.
    # This handles both file-system layouts and zipped wheels via the
    # context-manager dance that importlib.resources requires.
    with importlib_resources.as_file(candidate) as path:
        return load_manifest_from_yaml(Path(path))
