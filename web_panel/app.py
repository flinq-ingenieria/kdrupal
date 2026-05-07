from __future__ import annotations

import os
import threading
import uuid
from pathlib import Path
from typing import Any

from flask import Flask, abort, redirect, render_template, request, url_for
from dotenv import load_dotenv

from webapp.db import Database
from webapp.services import DnsService, DrupalProvisioner, K8sService, ServiceError

BASE_DIR = Path(__file__).resolve().parent.parent
TEMPLATE_PATH = BASE_DIR / "drupal.template.yaml"

# Carga automática de .env desde web_panel/.env si existe
load_dotenv(Path(__file__).resolve().parent / ".env")


def create_app() -> Flask:
    app = Flask(__name__)

    panel_auth_token = os.getenv("PANEL_AUTH_TOKEN", "").strip()
    default_base_domain = os.getenv("DEFAULT_BASE_DOMAIN", "").strip()
    default_admin_pass = os.getenv("DEFAULT_ADMIN_PASS", "").strip()
    namespace_prefix = os.getenv("NAMESPACE_PREFIX", "drupal-").strip()
    sqlite_path = os.getenv("SQLITE_PATH", str(Path(__file__).resolve().parent / "data" / "panel.db"))
    dinahosting_api_url = os.getenv("DINAHOSTING_API_URL", "").strip()
    dinahosting_auth_user = os.getenv("DINAHOSTING_AUTH_USER", "").strip()
    dinahosting_auth_pwd = os.getenv("DINAHOSTING_AUTH_PWD", "").strip()
    dinahosting_dns_target = os.getenv("DINAHOSTING_DNS_TARGET", "").strip()
    dinahosting_dns_ttl = int(os.getenv("DINAHOSTING_DNS_TTL", "300").strip())

    db = Database(sqlite_path)

    if not default_base_domain:
        raise RuntimeError("DEFAULT_BASE_DOMAIN es obligatorio")
    if not default_admin_pass:
        raise RuntimeError("DEFAULT_ADMIN_PASS es obligatorio")
    if not dinahosting_api_url or not dinahosting_auth_user or not dinahosting_auth_pwd or not dinahosting_dns_target:
        raise RuntimeError("Faltan variables de Dinahosting obligatorias (URL, AUTH_USER, AUTH_PWD, DNS_TARGET)")

    k8s = K8sService(manifest_template_path=TEMPLATE_PATH)
    dns = DnsService(
        api_url=dinahosting_api_url,
        auth_user=dinahosting_auth_user,
        auth_pwd=dinahosting_auth_pwd,
        zone_domain=default_base_domain,
    )
    provisioner = DrupalProvisioner(
        k8s=k8s,
        dns=dns,
        namespace_prefix=namespace_prefix,
        default_base_domain=default_base_domain,
        default_admin_pass=default_admin_pass,
        dns_target=dinahosting_dns_target,
        dns_ttl=dinahosting_dns_ttl,
    )

    def extract_request_token() -> str:
        bearer = request.headers.get("Authorization", "")
        if bearer.startswith("Bearer "):
            return bearer[7:].strip()
        return request.headers.get("X-Auth-Token", "").strip() or request.values.get("auth_token", "").strip()

    @app.before_request
    def require_token() -> None:
        if request.endpoint == "static":
            return
        if not panel_auth_token:
            abort(500, "Falta configurar PANEL_AUTH_TOKEN en el entorno")
        if extract_request_token() != panel_auth_token:
            abort(401, "No autorizado")

    def append_log(job_id: str, line: str) -> None:
        db.append_job_log(job_id, line)

    def run_create_job(job_id: str, site_id: str, payload: dict[str, str]) -> None:
        try:
            spec = provisioner.build_spec(payload, site_id=site_id)

            db.insert_site(
                {
                    "site_id": site_id,
                    "slug": spec.slug,
                    "namespace": spec.namespace,
                    "domain": spec.domain,
                    "www_mode": spec.www_mode,
                    "base_domain": spec.base_domain,
                    "status": "provisioning",
                    "admin_user": spec.admin_user,
                    "admin_email": spec.admin_email,
                    "admin_password": spec.admin_password,
                    "created_at": Database.utcnow(),
                    "deleted_at": None,
                }
            )

            append_log(job_id, f"Site: {spec.slug} namespace={spec.namespace} domain={spec.domain}")
            provisioner.provision(spec, lambda msg: append_log(job_id, msg))
            db.update_site_status(site_id, "active")
            db.update_job(job_id, "success", 0)
        except Exception as exc:
            db.update_site_status(site_id, "failed")
            append_log(job_id, f"ERROR: {exc}")
            db.update_job(job_id, "failed", 1)

    def run_delete_job(job_id: str, site_id: str) -> None:
        site = db.get_site(site_id)
        if not site:
            db.append_job_log(job_id, "ERROR: site no encontrado")
            db.update_job(job_id, "failed", 1)
            return
        try:
            db.update_site_status(site_id, "deleting")
            provisioner.delete_site(site["namespace"], site["domain"], lambda msg: append_log(job_id, msg))
            db.soft_delete_site(site_id, "deleted")
            db.update_job(job_id, "success", 0)
        except Exception as exc:
            append_log(job_id, f"ERROR: {exc}")
            db.update_site_status(site_id, "delete_failed")
            db.update_job(job_id, "failed", 1)

    @app.get("/")
    def index() -> str:
        sites = db.list_sites()
        jobs = db.list_jobs(limit=20)
        return render_template(
            "index.html",
            sites=sites,
            jobs=jobs,
            auth_token=extract_request_token(),
            namespace_prefix=namespace_prefix,
            default_base_domain=default_base_domain,
        )

    @app.post("/sites")
    def create_site() -> Any:
        site_slug = request.form.get("site_slug", "").strip()
        if not site_slug:
            abort(400, "site_slug es obligatorio")

        if db.get_site_by_slug(site_slug):
            abort(409, f"Ya existe un sitio activo con slug '{site_slug}'")

        payload = {
            "site_slug": site_slug,
            "domain": request.form.get("domain", "").strip(),
            "www_mode": request.form.get("www_mode", "auto").strip(),
            "site_name": request.form.get("site_name", "Mi Drupal").strip(),
            "locale": request.form.get("locale", "es").strip(),
            "admin_user": request.form.get("admin_user", "admin").strip(),
            "admin_email": request.form.get("admin_email", "").strip(),
            "modules": request.form.get("modules", "redirect").strip(),
        }

        site_id = str(uuid.uuid4())
        job_id = str(uuid.uuid4())
        db.insert_job(
            {
                "job_id": job_id,
                "site_id": site_id,
                "type": "create",
                "status": "running",
                "return_code": None,
                "created_at": Database.utcnow(),
                "finished_at": None,
            }
        )

        thread = threading.Thread(target=run_create_job, args=(job_id, site_id, payload), daemon=True)
        thread.start()

        return redirect(url_for("job_detail", job_id=job_id, auth_token=extract_request_token()))

    @app.post("/sites/<site_id>/delete")
    def delete_site(site_id: str) -> Any:
        site = db.get_site(site_id)
        if not site:
            abort(404)

        confirmation = request.form.get("confirm_slug", "").strip()
        if confirmation != site["slug"]:
            abort(400, "Confirmación inválida: debes escribir el slug exacto")

        if not site["namespace"].startswith(namespace_prefix):
            abort(400, "El sitio no pertenece al prefijo gestionado")

        job_id = str(uuid.uuid4())
        db.insert_job(
            {
                "job_id": job_id,
                "site_id": site_id,
                "type": "delete",
                "status": "running",
                "return_code": None,
                "created_at": Database.utcnow(),
                "finished_at": None,
            }
        )

        thread = threading.Thread(target=run_delete_job, args=(job_id, site_id), daemon=True)
        thread.start()

        return redirect(url_for("job_detail", job_id=job_id, auth_token=extract_request_token()))

    @app.get("/jobs/<job_id>")
    def job_detail(job_id: str) -> str:
        job = db.get_job(job_id)
        if not job:
            abort(404)

        site = db.get_site(job["site_id"]) if job.get("site_id") else None
        return render_template("job.html", job=job, site=site, auth_token=extract_request_token())

    @app.errorhandler(ServiceError)
    def handle_service_error(err: ServiceError):
        return str(err), 400

    return app


app = create_app()


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080, debug=True)
