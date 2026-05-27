from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_deployment_readme_and_required_files_exist() -> None:
    required = [
        "Dockerfile",
        "docker-compose.yml",
        "docker-compose.dev.yml",
        ".env.example",
        "deployment/README.md",
        "deployment/uvicorn_config.py",
        "deployment/nginx.example.conf",
        "deployment/systemd/molecule-ranker.service",
        "deployment/systemd/molecule-ranker-worker.service",
        "deployment/k8s/deployment.yaml",
        "deployment/k8s/service.yaml",
        "deployment/k8s/ingress.yaml",
        "deployment/k8s/secret.example.yaml",
        "deployment/scripts/entrypoint.sh",
        "deployment/scripts/wait_for_db.py",
    ]

    missing = [path for path in required if not (ROOT / path).exists()]

    assert missing == []


def test_env_example_does_not_contain_real_secrets() -> None:
    text = (ROOT / ".env.example").read_text()

    assert "replace-with" in text
    assert not re.search(r"sk-[A-Za-z0-9_-]{16,}", text)
    assert not re.search(r"gh[pousr]_[A-Za-z0-9_]{20,}", text)
    assert "-----BEGIN" not in text
    for line in text.splitlines():
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        if any(marker in key for marker in ["PASSWORD", "SECRET", "TOKEN"]):
            assert "replace-with" in value or "false" in value.lower()


def test_dockerfile_uses_non_root_user_and_no_secret_env() -> None:
    text = (ROOT / "Dockerfile").read_text()

    assert "USER molecule-ranker" in text
    assert "HEALTHCHECK" in text
    assert "VOLUME" in text
    assert "COPY . ." not in text
    assert "MOLECULE_RANKER_AUTH_SECRET=" not in text
    assert "OPENAI_API_KEY" not in text


def test_dockerfile_lint_if_hadolint_available() -> None:
    hadolint = shutil.which("hadolint")
    if hadolint is None:
        return

    completed = subprocess.run(
        [hadolint, str(ROOT / "Dockerfile")],
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr
