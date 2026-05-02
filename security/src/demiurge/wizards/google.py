"""Google Cloud onboarding wizard.

Drives the API-able GCP setup steps via ``gcloud`` subprocess and walks
the operator through the irreducible manual ones (OAuth consent screen,
OAuth Desktop client creation, JSON download).

Manual surface shrinks from ~8 steps to 3.
"""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple


log = logging.getLogger(__name__)


# Default scopes we need for Gmail + Calendar.
_DEFAULT_SCOPES = (
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/calendar",
    "https://www.googleapis.com/auth/calendar.events",
)


class WizardError(Exception):
    """Raised when the wizard can't proceed (bad gcloud state, operator abort, etc.)."""


@dataclass(frozen=True)
class GcloudStatus:
    installed: bool
    account: Optional[str] = None        # currently-authed gcloud account, or None
    project: Optional[str] = None        # currently-set default project, or None
    version: Optional[str] = None
    install_hint: Optional[str] = None   # set when installed=False


def check_gcloud(*, runner: Optional[Callable] = None) -> GcloudStatus:
    """Return the operator's gcloud status. Pure-ish — only runs gcloud info commands."""
    runner = runner or _default_runner
    if shutil.which("gcloud") is None:
        return GcloudStatus(
            installed=False,
            install_hint=(
                "gcloud not found on PATH. Install: "
                "https://cloud.google.com/sdk/docs/install — then run "
                "`gcloud auth login` once."
            ),
        )
    # Version
    version = None
    try:
        out = runner(["gcloud", "version", "--format=value(Google Cloud SDK)"])
        if out.returncode == 0:
            version = out.stdout.strip().splitlines()[0] if out.stdout else None
    except Exception:  # noqa: BLE001
        pass
    # Account + project
    account = None
    project = None
    try:
        out = runner(["gcloud", "config", "list", "--format=json"])
        if out.returncode == 0 and out.stdout:
            cfg = json.loads(out.stdout)
            core = (cfg or {}).get("core") or {}
            account = core.get("account")
            project = core.get("project")
    except Exception:  # noqa: BLE001
        pass
    return GcloudStatus(
        installed=True, account=account, project=project, version=version,
    )


def _default_runner(args: List[str]) -> subprocess.CompletedProcess:
    return subprocess.run(args, capture_output=True, text=True, check=False)


def run_gcloud(
    args: List[str], *, runner: Optional[Callable] = None,
) -> subprocess.CompletedProcess:
    runner = runner or _default_runner
    return runner(["gcloud"] + args)


# --- project + API enable ---


def list_projects(*, runner: Optional[Callable] = None) -> List[Dict[str, str]]:
    out = run_gcloud(["projects", "list", "--format=json"], runner=runner)
    if out.returncode != 0:
        raise WizardError(f"gcloud projects list failed: {out.stderr.strip()}")
    try:
        rows = json.loads(out.stdout or "[]")
    except json.JSONDecodeError as e:
        raise WizardError(f"gcloud projects list returned non-JSON: {e}")
    return [
        {"project_id": r.get("projectId", ""), "name": r.get("name", "")}
        for r in rows if isinstance(r, dict)
    ]


def create_project(
    project_id: str, *, name: Optional[str] = None,
    runner: Optional[Callable] = None,
) -> None:
    args = ["projects", "create", project_id]
    if name:
        args += ["--name", name]
    out = run_gcloud(args, runner=runner)
    if out.returncode != 0:
        # 409-equivalent: "already exists" — treat as idempotent reuse.
        if "already" in (out.stderr or "").lower():
            log.info("project %s already exists, reusing", project_id)
            return
        raise WizardError(f"gcloud projects create failed: {out.stderr.strip()}")


def enable_apis(
    project_id: str, apis: Tuple[str, ...] = (
        "gmail.googleapis.com", "calendar-json.googleapis.com", "pubsub.googleapis.com",
    ),
    *, runner: Optional[Callable] = None,
) -> None:
    out = run_gcloud(
        ["services", "enable", *apis, "--project", project_id],
        runner=runner,
    )
    if out.returncode != 0:
        raise WizardError(f"gcloud services enable failed: {out.stderr.strip()}")


# --- manual-step guides ---


@dataclass(frozen=True)
class ManualStep:
    title: str
    url: str
    instructions: List[str] = field(default_factory=list)


def consent_screen_step(project_id: str) -> ManualStep:
    return ManualStep(
        title="Configure OAuth consent screen",
        url=f"https://console.cloud.google.com/apis/credentials/consent?project={project_id}",
        instructions=[
            "Click the link above (or paste in your browser).",
            "User type: External.",
            "Publishing status (after creating): set to 'In production' — this gives long-lived refresh tokens.",
            "App name: 'Stevens' (or whatever you want to see in the consent prompt).",
            "User support email + Developer contact: your email.",
            "Scopes: add the following sensitive scopes:",
            *(f"  - {s}" for s in _DEFAULT_SCOPES),
            "Test users (only if you stay in 'Testing' mode): add your Google account.",
            "Save and continue through every page until done.",
        ],
    )


def oauth_client_step(project_id: str) -> ManualStep:
    return ManualStep(
        title="Create Desktop OAuth client + download JSON",
        url=f"https://console.cloud.google.com/apis/credentials?project={project_id}",
        instructions=[
            "Click the link above.",
            "Click '+ Create credentials' → 'OAuth client ID'.",
            "Application type: 'Desktop application'.",
            "Name: 'Stevens Desktop' (any name; for your reference).",
            "Click Create.",
            "In the popup, click 'Download JSON'. Save it to your Downloads folder.",
            "The wizard will detect the new file and continue automatically.",
        ],
    )


def wait_for_client_json(
    *,
    downloads_dir: Path,
    timeout_s: int = 600,
    poll_interval_s: float = 2.0,
    clock: Callable[[], float] = time.monotonic,
) -> Path:
    """Poll ``downloads_dir`` for a new ``client_secret*.json`` file.

    Records the set of pre-existing matching files, then polls for any
    new one. Returns the path of the newest new match.
    """
    if not downloads_dir.exists():
        raise WizardError(f"downloads dir not found: {downloads_dir}")
    pre_existing = set(downloads_dir.glob("client_secret*.json"))
    deadline = clock() + timeout_s
    while clock() < deadline:
        current = set(downloads_dir.glob("client_secret*.json"))
        new = current - pre_existing
        if new:
            # Pick the newest by mtime.
            return max(new, key=lambda p: p.stat().st_mtime)
        time.sleep(poll_interval_s)
    raise WizardError(
        f"timed out after {timeout_s}s waiting for client_secret*.json in {downloads_dir}"
    )


# --- pub/sub ---


_GMAIL_PUBSUB_SA = "gmail-api-push@system.gserviceaccount.com"


def topic_exists(
    project_id: str, topic: str, *, runner: Optional[Callable] = None,
) -> bool:
    out = run_gcloud(
        ["pubsub", "topics", "describe", topic, "--project", project_id, "--format=value(name)"],
        runner=runner,
    )
    return out.returncode == 0


def create_pubsub_topic(
    project_id: str, topic: str = "gmail-push", *, runner: Optional[Callable] = None,
) -> str:
    """Returns the full topic resource path."""
    if topic_exists(project_id, topic, runner=runner):
        log.info("pubsub topic %s already exists, reusing", topic)
    else:
        out = run_gcloud(
            ["pubsub", "topics", "create", topic, "--project", project_id],
            runner=runner,
        )
        if out.returncode != 0:
            raise WizardError(f"gcloud pubsub topics create failed: {out.stderr.strip()}")
    return f"projects/{project_id}/topics/{topic}"


def grant_gmail_pubsub_publisher(
    project_id: str, topic: str = "gmail-push", *, runner: Optional[Callable] = None,
) -> None:
    out = run_gcloud(
        [
            "pubsub", "topics", "add-iam-policy-binding", topic,
            "--project", project_id,
            "--member", f"serviceAccount:{_GMAIL_PUBSUB_SA}",
            "--role", "roles/pubsub.publisher",
        ],
        runner=runner,
    )
    if out.returncode != 0:
        raise WizardError(f"gcloud pubsub IAM grant failed: {out.stderr.strip()}")


def subscription_exists(
    project_id: str, subscription: str, *, runner: Optional[Callable] = None,
) -> bool:
    out = run_gcloud(
        ["pubsub", "subscriptions", "describe", subscription,
         "--project", project_id, "--format=value(name)"],
        runner=runner,
    )
    return out.returncode == 0


def create_push_subscription(
    project_id: str, *,
    topic: str = "gmail-push",
    subscription: str = "gmail-push-sub",
    push_endpoint: str,
    runner: Optional[Callable] = None,
) -> str:
    """Returns the full subscription resource path."""
    if subscription_exists(project_id, subscription, runner=runner):
        log.info("pubsub subscription %s already exists, reusing", subscription)
    else:
        out = run_gcloud(
            [
                "pubsub", "subscriptions", "create", subscription,
                "--project", project_id,
                "--topic", topic,
                "--push-endpoint", push_endpoint,
            ],
            runner=runner,
        )
        if out.returncode != 0:
            raise WizardError(f"gcloud pubsub subscriptions create failed: {out.stderr.strip()}")
    return f"projects/{project_id}/subscriptions/{subscription}"


# --- orchestration ---


@dataclass
class WizardInputs:
    """Operator-supplied values + optional callbacks for I/O.

    For tests, supply ``confirm`` and ``ask`` to script operator interaction.
    Defaults use real prompts.
    """

    project_id: str
    project_name: Optional[str] = None
    push_endpoint: Optional[str] = None    # asked interactively if None
    downloads_dir: Optional[Path] = None
    runner: Optional[Callable] = None
    confirm: Callable[[str], bool] = lambda prompt: input(f"{prompt} [y/N] ").strip().lower() == "y"
    ask: Callable[[str], str] = input
    say: Callable[[str], None] = print


@dataclass
class WizardResult:
    project_id: str
    topic_path: str
    subscription_path: str
    client_secret_path: Path


def run_wizard(inputs: WizardInputs) -> WizardResult:
    """Top-level wizard. Each side-effecting step prompts for confirmation."""
    say = inputs.say
    confirm = inputs.confirm

    # Prereqs
    status = check_gcloud(runner=inputs.runner)
    if not status.installed:
        raise WizardError(status.install_hint or "gcloud missing")
    if not status.account:
        raise WizardError(
            "gcloud is installed but no account is logged in. "
            "Run `gcloud auth login` first, then re-run this wizard."
        )
    say(f"gcloud OK — account={status.account} version={status.version}")

    # Project
    if confirm(f"Create or reuse GCP project {inputs.project_id!r}?"):
        create_project(inputs.project_id, name=inputs.project_name, runner=inputs.runner)
        say(f"project {inputs.project_id} ready")
    else:
        raise WizardError("operator aborted at project step")

    # APIs
    if confirm("Enable Gmail / Calendar / Pub/Sub APIs?"):
        enable_apis(inputs.project_id, runner=inputs.runner)
        say("APIs enabled")
    else:
        raise WizardError("operator aborted at API-enable step")

    # OAuth consent (manual)
    say("\n=== Manual step 1: OAuth consent screen ===")
    consent = consent_screen_step(inputs.project_id)
    say(f"  {consent.title}")
    say(f"  URL: {consent.url}")
    for line in consent.instructions:
        say(f"  - {line}")
    if not confirm("Done with OAuth consent screen?"):
        raise WizardError("operator aborted at OAuth consent step")

    # OAuth client (manual)
    say("\n=== Manual step 2: OAuth client + JSON download ===")
    client = oauth_client_step(inputs.project_id)
    say(f"  {client.title}")
    say(f"  URL: {client.url}")
    for line in client.instructions:
        say(f"  - {line}")
    say("Waiting for client_secret*.json in your Downloads folder...")
    downloads = inputs.downloads_dir or (Path.home() / "Downloads")
    json_path = wait_for_client_json(downloads_dir=downloads)
    say(f"detected client JSON: {json_path}")

    # Pub/Sub topic + IAM + subscription
    push_endpoint = inputs.push_endpoint or inputs.ask(
        "Public push-receiver URL for Gmail webhook (e.g. https://stevens.example.ts.net/gmail/push): "
    ).strip()
    if not push_endpoint:
        raise WizardError("push endpoint required")
    if confirm(f"Create Pub/Sub topic + grant Gmail publisher + create push subscription to {push_endpoint}?"):
        topic_path = create_pubsub_topic(inputs.project_id, runner=inputs.runner)
        grant_gmail_pubsub_publisher(inputs.project_id, runner=inputs.runner)
        sub_path = create_push_subscription(
            inputs.project_id, push_endpoint=push_endpoint, runner=inputs.runner,
        )
        say(f"pubsub: topic={topic_path} subscription={sub_path}")
    else:
        raise WizardError("operator aborted at pubsub step")

    return WizardResult(
        project_id=inputs.project_id,
        topic_path=topic_path,
        subscription_path=sub_path,
        client_secret_path=json_path,
    )
