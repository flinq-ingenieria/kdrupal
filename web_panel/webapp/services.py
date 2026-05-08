from __future__ import annotations

import re
import secrets
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import requests
import yaml


LogFn = Callable[[str], None]


class ServiceError(RuntimeError):
    pass


def normalize_slug(value: str) -> str:
    slug = re.sub(r"[^a-z0-9-]", "-", value.strip().lower())
    slug = re.sub(r"-+", "-", slug).strip("-")
    if not slug:
        raise ServiceError("site_slug inválido")
    return slug


def is_valid_domain(domain: str) -> bool:
    return bool(re.match(r"^[a-zA-Z0-9][a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$", domain))


def include_www(domain: str, www_mode: str) -> bool:
    if www_mode == "with":
        return True
    if www_mode == "without":
        return False
    if www_mode != "auto":
        raise ServiceError(f"WWW mode inválido: {www_mode}")
    return domain.count(".") == 1


def random_subdomain(base_domain: str) -> str:
    return f"drupal-{secrets.token_hex(4)}.{base_domain}"


def random_hex(length: int) -> str:
    return secrets.token_hex(length // 2)


@dataclass
class SiteSpec:
    site_id: str
    slug: str
    namespace: str
    domain: str
    base_domain: str
    www_mode: str
    include_www: bool
    admin_user: str
    admin_email: str
    admin_password: str
    drupal_app_image: str
    image_pull_secret_name: str


class DnsService:
    def __init__(self, api_url: str, auth_user: str, auth_pwd: str, zone_domain: str, timeout: int = 30) -> None:
        self.api_url = api_url.rstrip("/")
        self.auth_user = auth_user
        self.auth_pwd = auth_pwd
        self.zone_domain = zone_domain
        self.timeout = timeout

    def create_record(self, fqdn: str, target: str, ttl: int, log: LogFn) -> None:
        hostname = self._hostname_for_zone(fqdn)
        params = {
            "domain": self.zone_domain,
            "hostname": hostname,
            "ip": target,
            "ttl": ttl,
            "command": "Domain_Zone_AddTypeA",
        }
        self._call_api(params, log, "create")

    def delete_record(self, fqdn: str, target: str, log: LogFn) -> None:
        hostname = self._hostname_for_zone(fqdn)
        params = {
            "domain": self.zone_domain,
            "hostname": hostname,
            "ip": target,
            "command": "Domain_Zone_DeleteTypeA",
        }
        self._call_api(params, log, "delete")

    def _hostname_for_zone(self, fqdn: str) -> str:
        suffix = "." + self.zone_domain
        if fqdn == self.zone_domain:
            return ""
        if not fqdn.endswith(suffix):
            raise ServiceError(f"El dominio '{fqdn}' no pertenece a la zona '{self.zone_domain}'")
        return fqdn[: -len(suffix)]

    def _call_api(self, params: dict, log: LogFn, action: str) -> None:
        log(f"Dinahosting DNS {action}: {params['hostname']}.{params['domain']}".strip("."))
        form = {
            "AUTH_USER": self.auth_user,
            "AUTH_PWD": self.auth_pwd,
        }
        response = requests.post(
            self.api_url,
            params=params,
            data=form,
            timeout=self.timeout,
        )
        if response.status_code >= 400:
            raise ServiceError(f"DNS API error HTTP {response.status_code}: {response.text[:500]}")

        body = response.text.strip()
        if not body:
            return
        # La API de Dinahosting puede responder texto/plano o XML/JSON según comando/configuración.
        if "ERROR" in body.upper():
            raise ServiceError(f"DNS API error: {body[:500]}")


class K8sService:
    def __init__(self, manifest_template_path: Path, deploy_timeout_seconds: int = 900) -> None:
        self.manifest_template_path = manifest_template_path
        self.deploy_timeout_seconds = deploy_timeout_seconds
        try:
            from kubernetes import client, config, stream, utils
            from kubernetes.client import ApiException
        except Exception as exc:
            raise ServiceError(f"No se pudo importar kubernetes client: {exc}") from exc
        self._client = client
        self._stream = stream
        self._utils = utils
        self._api_exception = ApiException
        try:
            config.load_incluster_config()
        except Exception:
            config.load_kube_config()

        self.core = self._client.CoreV1Api()
        self.apps = self._client.AppsV1Api()

    def namespace_exists(self, namespace: str) -> bool:
        try:
            self.core.read_namespace(namespace)
            return True
        except self._api_exception as exc:
            if exc.status == 404:
                return False
            raise

    def secret_exists(self, namespace: str, name: str) -> bool:
        try:
            self.core.read_namespaced_secret(name, namespace)
            return True
        except self._api_exception as exc:
            if exc.status == 404:
                return False
            raise

    def ensure_namespace(self, namespace: str) -> None:
        if self.namespace_exists(namespace):
            return
        body = self._client.V1Namespace(metadata=self._client.V1ObjectMeta(name=namespace))
        try:
            self.core.create_namespace(body)
        except self._api_exception as exc:
            if exc.status == 409:
                return
            raise

    def ensure_image_pull_secret(
        self,
        namespace: str,
        secret_name: str,
        registry: str,
        username: str,
        password: str,
        email: str,
        log: LogFn,
    ) -> None:
        auth = {
            "auths": {
                registry: {
                    "username": username,
                    "password": password,
                    "email": email,
                }
            }
        }
        body = self._client.V1Secret(
            metadata=self._client.V1ObjectMeta(name=secret_name, namespace=namespace),
            type="kubernetes.io/dockerconfigjson",
            string_data={".dockerconfigjson": json.dumps(auth)},
        )
        try:
            self.core.create_namespaced_secret(namespace=namespace, body=body)
            log(f"Image pull secret creado: {secret_name}")
        except self._api_exception as exc:
            if exc.status != 409:
                raise
            self.core.replace_namespaced_secret(name=secret_name, namespace=namespace, body=body)
            log(f"Image pull secret actualizado: {secret_name}")

    def read_existing_credentials(self, namespace: str) -> dict[str, str]:
        secret = self.core.read_namespaced_secret("drupalcms-secrets", namespace)
        data = secret.data or {}

        import base64

        def b64(key: str) -> str:
            if key not in data:
                return ""
            return base64.b64decode(data[key]).decode("utf-8")

        return {
            "mariadb_root_password": b64("mariadb-root-password"),
            "mariadb_password": b64("mariadb-password"),
            "drupal_hash_salt": b64("drupal-hash-salt"),
        }

    def apply_resources(self, spec: SiteSpec, creds: dict[str, str], log: LogFn) -> None:
        replacements = {
            "NAMESPACE": spec.namespace,
            "DOMAIN": spec.domain,
            "MARIADB_ROOT_PASSWORD": creds["mariadb_root_password"],
            "MARIADB_PASSWORD": creds["mariadb_password"],
            "DRUPAL_HASH_SALT": creds["drupal_hash_salt"],
            "TLS_SECRET_NAME": spec.domain.replace(".", "-") + "-tls",
            "DOMAIN_REGEX": spec.domain.replace(".", "\\\\."),
            "DRUPAL_APP_IMAGE": spec.drupal_app_image,
            "IMAGE_PULL_SECRET_NAME": spec.image_pull_secret_name,
        }

        raw = self.manifest_template_path.read_text()
        for key, value in replacements.items():
            raw = raw.replace("${" + key + "}", value)

        if spec.include_www:
            raw = re.sub(r"[ \t]*#WWW_ONLY", "", raw)
        else:
            raw = "\n".join(line for line in raw.splitlines() if "#WWW_ONLY" not in line)

        docs = list(yaml.safe_load_all(raw))
        for doc in docs:
            if not doc:
                continue
            if doc.get("kind") == "Namespace":
                continue
            self._apply_resource(doc)

        log("Recursos Kubernetes aplicados")

    def _apply_resource(self, doc: dict) -> None:
        kind = doc.get("kind")
        metadata = doc.get("metadata") or {}
        name = metadata.get("name")
        namespace = metadata.get("namespace")
        if not kind or not name:
            raise ServiceError(f"Manifiesto inválido sin kind/name: {doc}")

        try:
            self._utils.create_from_dict(self._client.ApiClient(), data=doc, verbose=False)
            return
        except self._api_exception as exc:
            if exc.status != 409:
                raise

        if kind == "Secret":
            # drupalcms-secrets contiene credenciales existentes; no las regeneramos en reapply.
            return
        if kind == "PersistentVolumeClaim":
            return
        if kind == "ConfigMap":
            self.core.replace_namespaced_config_map(name, namespace, doc)
            return
        if kind == "Service":
            current = self.core.read_namespaced_service(name, namespace)
            doc["spec"]["clusterIP"] = current.spec.cluster_ip
            doc["spec"].pop("clusterIPs", None)
            doc["spec"].pop("ipFamilies", None)
            doc["spec"].pop("ipFamilyPolicy", None)
            self.core.replace_namespaced_service(name, namespace, doc)
            return
        if kind == "Deployment":
            self.apps.patch_namespaced_deployment(name, namespace, doc)
            return
        if kind == "StatefulSet":
            self.apps.patch_namespaced_stateful_set(name, namespace, doc)
            return
        if kind == "Ingress":
            networking = self._client.NetworkingV1Api()
            networking.patch_namespaced_ingress(name, namespace, doc)
            return

        raise ServiceError(f"Recurso existente no soportado para apply idempotente: {kind}/{name}")

    def wait_ready(self, namespace: str, log: LogFn) -> None:
        try:
            from kubernetes import watch
        except Exception as exc:
            raise ServiceError(f"No se pudo importar kubernetes.watch: {exc}") from exc

        timeout = self.deploy_timeout_seconds

        w = watch.Watch()
        for event in w.stream(
            self.apps.list_namespaced_stateful_set,
            namespace=namespace,
            timeout_seconds=timeout,
        ):
            obj = event["object"]
            if obj.metadata.name == "mariadb":
                ready = obj.status.ready_replicas or 0
                if ready >= 1:
                    w.stop()
        log("StatefulSet mariadb listo")

        w2 = watch.Watch()
        for event in w2.stream(
            self.apps.list_namespaced_deployment,
            namespace=namespace,
            timeout_seconds=timeout,
        ):
            obj = event["object"]
            if obj.metadata.name == "drupalcms":
                ready = obj.status.ready_replicas or 0
                if ready >= 1:
                    w2.stop()
        log("Deployment drupalcms listo")

    def delete_namespace(self, namespace: str, log: LogFn) -> None:
        try:
            self.core.delete_namespace(namespace)
            log(f"Namespace {namespace} eliminado")
        except self._api_exception as exc:
            if exc.status == 404:
                log(f"Namespace {namespace} no existe (borrado idempotente)")
                return
            raise

    def scale_site(self, namespace: str, running: bool, log: LogFn) -> None:
        replicas = 1 if running else 0
        self.apps.patch_namespaced_deployment_scale(
            name="drupalcms",
            namespace=namespace,
            body={"spec": {"replicas": replicas}},
        )
        self.apps.patch_namespaced_stateful_set_scale(
            name="mariadb",
            namespace=namespace,
            body={"spec": {"replicas": replicas}},
        )
        state = "arrancado" if running else "detenido"
        log(f"Escala aplicada: drupalcms={replicas}, mariadb={replicas} ({state})")

    def run_drupal_command(self, namespace: str, command: str, log: LogFn) -> None:
        pods = self.core.list_namespaced_pod(namespace=namespace, label_selector="app=drupalcms").items
        pod = next((item for item in pods if item.status.phase == "Running"), None)
        if not pod:
            raise ServiceError(f"No hay pod drupalcms en ejecución en {namespace}")

        pod_name = pod.metadata.name
        log(f"Ejecutando en {namespace}/{pod_name}: {command}")
        output = self._stream.stream(
            self.core.connect_get_namespaced_pod_exec,
            pod_name,
            namespace,
            container="app",
            command=[
                "/bin/sh",
                "-lc",
                f"cd /var/www/html/app && {command}; rc=$?; echo __KDRUPAL_EXIT_CODE:$rc; exit $rc",
            ],
            stderr=True,
            stdin=False,
            stdout=True,
            tty=False,
        )
        exit_code = 0
        if output:
            for line in output.splitlines():
                if line.startswith("__KDRUPAL_EXIT_CODE:"):
                    exit_code = int(line.split(":", 1)[1])
                    continue
                log(line)
        if exit_code != 0:
            raise ServiceError(f"Comando Drupal falló con exit code {exit_code}")


class DrupalProvisioner:
    def __init__(
        self,
        k8s: K8sService,
        dns: DnsService,
        namespace_prefix: str,
        default_base_domain: str,
        default_admin_pass: str,
        drupal_app_image: str,
        image_pull_secret_name: str,
        ghcr_registry: str,
        ghcr_username: str,
        ghcr_token: str,
        ghcr_email: str,
        dns_target: str,
        dns_ttl: int,
    ) -> None:
        self.k8s = k8s
        self.dns = dns
        self.namespace_prefix = namespace_prefix
        self.default_base_domain = default_base_domain
        self.default_admin_pass = default_admin_pass
        self.drupal_app_image = drupal_app_image
        self.image_pull_secret_name = image_pull_secret_name
        self.ghcr_registry = ghcr_registry
        self.ghcr_username = ghcr_username
        self.ghcr_token = ghcr_token
        self.ghcr_email = ghcr_email
        self.dns_target = dns_target
        self.dns_ttl = dns_ttl

    def build_spec(self, payload: dict[str, str], site_id: str) -> SiteSpec:
        if not payload.get("site_slug", "").strip():
            raise ServiceError("site_slug es obligatorio")
        slug = normalize_slug(payload.get("site_slug", ""))
        namespace = f"{self.namespace_prefix}{slug}"
        domain = (payload.get("domain") or "").strip()
        if not domain:
            domain = random_subdomain(self.default_base_domain)
        if not is_valid_domain(domain):
            raise ServiceError(f"Dominio inválido: {domain}")

        www_mode = payload.get("www_mode", "auto").strip()
        include = include_www(domain, www_mode)

        admin_user = "pending-wizard"
        admin_email = f"pending@{domain}"
        admin_password = self.default_admin_pass

        return SiteSpec(
            site_id=site_id,
            slug=slug,
            namespace=namespace,
            domain=domain,
            base_domain=self.default_base_domain,
            www_mode=www_mode,
            include_www=include,
            admin_user=admin_user,
            admin_email=admin_email,
            admin_password=admin_password,
            drupal_app_image=self.drupal_app_image,
            image_pull_secret_name=self.image_pull_secret_name,
        )

    def provision(self, spec: SiteSpec, log: LogFn) -> None:
        def stage(name: str, message: str) -> None:
            log(f"[{name}] {message}")

        stage("VALIDATION", f"Inicio provisioning slug={spec.slug} namespace={spec.namespace} domain={spec.domain}")
        namespace_exists = self.k8s.namespace_exists(spec.namespace)
        creds: dict[str, str]
        if namespace_exists and self.k8s.secret_exists(spec.namespace, "drupalcms-secrets"):
            stage("VALIDATION", "Namespace existente: reutilizando credenciales de drupalcms-secrets")
            old = self.k8s.read_existing_credentials(spec.namespace)
            creds = {
                "mariadb_root_password": old["mariadb_root_password"],
                "mariadb_password": old["mariadb_password"],
                "drupal_hash_salt": old["drupal_hash_salt"],
            }
        else:
            creds = {
                "mariadb_root_password": random_hex(32),
                "mariadb_password": random_hex(32),
                "drupal_hash_salt": random_hex(64),
            }
            stage("VALIDATION", "Namespace nuevo o sin secret: generando credenciales DB/hash_salt")
        stage(
            "DB_INFO",
            f"host=mariadb database=drupalcms user=drupal password={creds['mariadb_password']}",
        )

        if not namespace_exists:
            stage("DNS", f"Creando registro DNS para {spec.domain} -> {self.dns_target} (ttl={self.dns_ttl})")
            self.dns.create_record(spec.domain, self.dns_target, self.dns_ttl, log)
            stage("DNS", "Registro DNS creado")
        else:
            stage("DNS", "Namespace existente: se omite creación DNS")

        stage("NAMESPACE", "Asegurando namespace")
        self.k8s.ensure_namespace(spec.namespace)
        stage("NAMESPACE", "Namespace OK")
        stage("PULL_SECRET", f"Asegurando imagePullSecret '{spec.image_pull_secret_name}' en {spec.namespace}")
        self.k8s.ensure_image_pull_secret(
            namespace=spec.namespace,
            secret_name=spec.image_pull_secret_name,
            registry=self.ghcr_registry,
            username=self.ghcr_username,
            password=self.ghcr_token,
            email=self.ghcr_email,
            log=log,
        )
        stage("PULL_SECRET", "imagePullSecret OK")
        stage("APPLY", "Aplicando manifiestos Kubernetes")
        self.k8s.apply_resources(spec, creds, log)
        stage("APPLY", "Manifiestos aplicados")
        stage("WAIT_DB", "Esperando mariadb listo")
        self.k8s.wait_ready(spec.namespace, log)
        stage("WAIT_APP", "Pod de aplicación listo")
        stage("READY_FOR_WIZARD", f"Drupal listo para instalación web manual: https://{spec.domain}/")

    def delete_site(self, namespace: str, domain: str, log: LogFn) -> None:
        if not namespace.startswith(self.namespace_prefix):
            raise ServiceError(f"Namespace fuera de prefijo gestionado: {namespace}")
        self.k8s.delete_namespace(namespace, log)
        self.dns.delete_record(domain, self.dns_target, log)

    def set_site_running(self, namespace: str, running: bool, log: LogFn) -> None:
        if not namespace.startswith(self.namespace_prefix):
            raise ServiceError(f"Namespace fuera de prefijo gestionado: {namespace}")
        self.k8s.scale_site(namespace, running, log)

    def rebuild_cache(self, namespace: str, log: LogFn) -> None:
        if not namespace.startswith(self.namespace_prefix):
            raise ServiceError(f"Namespace fuera de prefijo gestionado: {namespace}")
        self.k8s.run_drupal_command(
            namespace,
            "./vendor/bin/drush cr || { echo 'Primer cache rebuild falló; reintentando...'; ./vendor/bin/drush cr; }",
            log,
        )
