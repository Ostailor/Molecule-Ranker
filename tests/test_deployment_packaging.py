from __future__ import annotations

import os
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
        "deployment/hardening.md",
        "deployment/docker-compose.enterprise.yml",
        "deployment/uvicorn_config.py",
        "deployment/nginx.example.conf",
        "deployment/systemd/molecule-ranker.service",
        "deployment/systemd/molecule-ranker-worker.service",
        "deployment/k8s/deployment.yaml",
        "deployment/k8s/codex-worker.yaml",
        "deployment/k8s/service.yaml",
        "deployment/k8s/ingress.yaml",
        "deployment/k8s/secret.example.yaml",
        "deployment/helm/Chart.yaml",
        "deployment/helm/values.yaml",
        "deployment/helm/templates/deployment.yaml",
        "deployment/helm/templates/service.yaml",
        "deployment/terraform/README.md",
        "deployment/terraform/main.tf",
        "deployment/terraform/variables.tf",
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


def test_v2_enterprise_compose_separates_server_worker_and_uses_secret_refs() -> None:
    text = (ROOT / "deployment/docker-compose.enterprise.yml").read_text()

    assert "molecule-ranker-server" in text
    assert "molecule-ranker-worker" in text
    assert "molecule-ranker-codex-worker" in text
    assert "user: \"1000:1000\"" in text
    assert "/health" in text
    assert "/ready" in text
    assert "postgres:16-alpine" in text
    assert "MOLECULE_RANKER_EXTERNAL_WRITES_ENABLED: \"false\"" in text
    assert "MOLECULE_RANKER_ENABLE_CODEX_WORKER: \"false\"" in text
    assert "source: auth_secret" in text
    assert "source: postgres_password" in text
    assert "artifact-storage:/data/artifacts" in text
    assert "resources:" in text


def test_v2_kubernetes_and_helm_manifests_include_hardening_controls() -> None:
    k8s = (ROOT / "deployment/k8s/deployment.yaml").read_text()
    codex = (ROOT / "deployment/k8s/codex-worker.yaml").read_text()
    helm_values = (ROOT / "deployment/helm/values.yaml").read_text()
    helm_deployment = (ROOT / "deployment/helm/templates/deployment.yaml").read_text()

    combined = "\n".join([k8s, codex, helm_values, helm_deployment])
    assert "molecule-ranker:2.2.0" in combined
    assert "runAsNonRoot: true" in combined
    assert "allowPrivilegeEscalation: false" in combined
    assert "readinessProbe:" in combined
    assert "livenessProbe:" in combined
    assert "resources:" in combined
    assert "secretKeyRef:" in combined
    assert "MOLECULE_RANKER_EXTERNAL_WRITES_ENABLED" in combined
    assert "MOLECULE_RANKER_ENABLE_CODEX_WORKER" in combined
    assert "molecule-ranker-codex-worker" in codex
    assert "optional: true" in codex


def test_v2_deployment_docs_cover_offline_backup_restore_and_limits() -> None:
    readme = (ROOT / "deployment/README.md").read_text()
    hardening = (ROOT / "deployment/hardening.md").read_text()
    terraform = (ROOT / "deployment/terraform/README.md").read_text()
    combined = "\n".join([readme, hardening, terraform])

    assert "V2.0" in readme
    assert "offline/local deployment" in combined
    assert "backup" in combined.lower()
    assert "restore" in combined.lower()
    assert "resource limits" in combined.lower()
    assert "external integration writes disabled by default" in combined.lower()
    assert "not a regulated clinical product" in combined.lower()


def test_deployment_examples_do_not_contain_plaintext_secrets() -> None:
    checked = [
        ROOT / ".env.example",
        ROOT / "deployment/docker-compose.enterprise.yml",
        ROOT / "deployment/k8s/deployment.yaml",
        ROOT / "deployment/k8s/codex-worker.yaml",
        ROOT / "deployment/k8s/secret.example.yaml",
        ROOT / "deployment/helm/values.yaml",
        ROOT / "deployment/helm/templates/deployment.yaml",
        ROOT / "deployment/terraform/main.tf",
        ROOT / "deployment/terraform/variables.tf",
    ]
    combined = "\n".join(path.read_text() for path in checked)

    assert not re.search(r"sk-[A-Za-z0-9_-]{16,}", combined)
    assert not re.search(r"gh[pousr]_[A-Za-z0-9_]{20,}", combined)
    assert "-----BEGIN" not in combined
    assert "password123" not in combined.lower()
    assert "secret-token" not in combined.lower()
    assert "changeme" not in combined.lower()


def test_enterprise_compose_config_if_docker_compose_available() -> None:
    docker = shutil.which("docker")
    if docker is None:
        return

    completed = subprocess.run(
        [
            docker,
            "compose",
            "-f",
            str(ROOT / "deployment/docker-compose.enterprise.yml"),
            "config",
        ],
        text=True,
        capture_output=True,
        check=False,
        timeout=120,
        env={
            **os.environ,
            "POSTGRES_PASSWORD_FILE": "/run/secrets/postgres_password",
            "MOLECULE_RANKER_AUTH_SECRET_FILE": "/run/secrets/auth_secret",
        },
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr


def test_docker_build_if_docker_available() -> None:
    docker = shutil.which("docker")
    if docker is None:
        return

    info = subprocess.run(
        [docker, "info"],
        text=True,
        capture_output=True,
        check=False,
        timeout=30,
    )
    if info.returncode != 0:
        return

    try:
        completed = subprocess.run(
            [
                docker,
                "build",
                "-f",
                str(ROOT / "deployment/Dockerfile"),
                "-t",
                "molecule-ranker:test-v2-deployment",
                str(ROOT),
            ],
            text=True,
            capture_output=True,
            check=False,
            timeout=120,
        )
    except subprocess.TimeoutExpired:
        return

    assert completed.returncode == 0, completed.stdout + completed.stderr
