from __future__ import annotations

import os
import subprocess
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from flask import Flask, abort, redirect, render_template, request, url_for

BASE_DIR = Path(__file__).resolve().parent.parent
SCRIPT_PATH = BASE_DIR / "deploy-drupal.sh"
PANEL_AUTH_TOKEN = os.getenv("PANEL_AUTH_TOKEN", "").strip()

app = Flask(__name__)


@dataclass
class DeploymentJob:
    id: str
    created_at: datetime
    params: dict[str, str]
    status: str = "queued"
    return_code: int | None = None
    logs: list[str] = field(default_factory=list)


jobs: dict[str, DeploymentJob] = {}
jobs_lock = threading.Lock()


def extract_request_token() -> str:
    bearer = request.headers.get("Authorization", "")
    if bearer.startswith("Bearer "):
        return bearer[7:].strip()
    return (
        request.headers.get("X-Auth-Token", "").strip()
        or request.values.get("auth_token", "").strip()
    )


@app.before_request
def require_token() -> None:
    if request.endpoint == "static":
        return
    if not PANEL_AUTH_TOKEN:
        abort(500, "Falta configurar PANEL_AUTH_TOKEN en el entorno")
    if extract_request_token() != PANEL_AUTH_TOKEN:
        abort(401, "No autorizado")


def append_log(job: DeploymentJob, line: str) -> None:
    with jobs_lock:
        job.logs.append(line.rstrip("\n"))
        if len(job.logs) > 5000:
            del job.logs[:1000]


def run_job(job_id: str) -> None:
    with jobs_lock:
        job = jobs[job_id]
        job.status = "running"

    env = os.environ.copy()
    env.update(job.params)

    command = ["bash", str(SCRIPT_PATH)]
    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        env=env,
        cwd=str(BASE_DIR),
    )

    assert process.stdout is not None
    for line in process.stdout:
        append_log(job, line)

    return_code = process.wait()

    with jobs_lock:
        job.return_code = return_code
        job.status = "success" if return_code == 0 else "failed"


@app.get("/")
def index() -> str:
    with jobs_lock:
        recent_jobs = sorted(jobs.values(), key=lambda j: j.created_at, reverse=True)
    return render_template("index.html", jobs=recent_jobs, auth_token=extract_request_token())


@app.post("/deploy")
def deploy() -> Any:
    if not SCRIPT_PATH.exists():
        abort(500, f"No existe el script: {SCRIPT_PATH}")

    domain = request.form.get("domain", "").strip()
    base_domain = request.form.get("base_domain", "").strip()
    namespace = request.form.get("namespace", "").strip()

    if not namespace:
        abort(400, "namespace es obligatorio")
    if not domain and not base_domain:
        abort(400, "Debes informar domain o base_domain")

    params = {
        "DOMAIN": domain,
        "BASE_DOMAIN": base_domain,
        "NAMESPACE": namespace,
        "DRUPAL_SITE_NAME": request.form.get("site_name", "Mi Drupal").strip(),
        "DRUPAL_LOCALE": request.form.get("locale", "es").strip(),
        "DRUPAL_ADMIN_USER": request.form.get("admin_user", "admin").strip(),
        "DRUPAL_ADMIN_EMAIL": request.form.get("admin_email", "").strip(),
        "DRUPAL_ENABLE_MODULES": request.form.get("modules", "redirect").strip(),
        "DEPLOY_TIMEOUT_SECONDS": request.form.get("timeout", "900").strip(),
        "AUTO_CONFIRM": "true",
    }

    if request.form.get("admin_pass", "").strip():
        params["DRUPAL_ADMIN_PASS"] = request.form.get("admin_pass", "").strip()

    if request.form.get("dns_target", "").strip():
        params["DNS_TARGET"] = request.form.get("dns_target", "").strip()
    if request.form.get("dns_ttl", "").strip():
        params["DNS_TTL"] = request.form.get("dns_ttl", "").strip()
    if request.form.get("dns_provider", "").strip():
        params["DNS_PROVIDER"] = request.form.get("dns_provider", "").strip()
    if request.form.get("dns_auth_token", "").strip():
        params["DNS_AUTH_TOKEN"] = request.form.get("dns_auth_token", "").strip()

    params = {k: v for k, v in params.items() if v != ""}

    job_id = str(uuid.uuid4())
    job = DeploymentJob(id=job_id, created_at=datetime.now(), params=params)

    with jobs_lock:
        jobs[job_id] = job

    thread = threading.Thread(target=run_job, args=(job_id,), daemon=True)
    thread.start()

    return redirect(url_for("job_detail", job_id=job_id, auth_token=extract_request_token()))


@app.get("/jobs/<job_id>")
def job_detail(job_id: str) -> str:
    with jobs_lock:
        job = jobs.get(job_id)
    if not job:
        abort(404)

    return render_template("job.html", job=job, auth_token=extract_request_token())


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080, debug=True)
