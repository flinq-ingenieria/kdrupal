from __future__ import annotations

import re
import secrets
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
    site_name: str
    locale: str
    admin_user: str
    admin_email: str
    admin_password: str
    modules: list[str]


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
            "drupal_admin_user": b64("drupal-admin-user"),
            "drupal_admin_email": b64("drupal-admin-email"),
            "drupal_admin_pass": b64("drupal-admin-pass"),
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
            "DRUPAL_ADMIN_USER": spec.admin_user,
            "DRUPAL_ADMIN_PASS": spec.admin_password,
            "DRUPAL_ADMIN_EMAIL": spec.admin_email,
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
            self._utils.create_from_dict(self._client.ApiClient(), data=doc, verbose=False)

        log("Recursos Kubernetes aplicados")

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

    def find_tools_pod(self, namespace: str) -> str:
        pods = self.core.list_namespaced_pod(namespace, label_selector="app=drupalcms")
        if not pods.items:
            raise ServiceError("No se encontró pod de drupalcms")
        return pods.items[0].metadata.name

    def exec_tools(self, namespace: str, pod: str, cmd: str) -> str:
        command = ["/bin/sh", "-lc", cmd]
        output = self._stream.stream(
            self.core.connect_get_namespaced_pod_exec,
            pod,
            namespace,
            container="tools",
            command=command,
            stderr=True,
            stdin=False,
            stdout=True,
            tty=False,
        )
        return output

    def bootstrap_drupal(self, spec: SiteSpec, log: LogFn) -> None:
        pod = self.find_tools_pod(spec.namespace)

        def run_www(inner: str) -> str:
            wrapped = f"su -s /bin/sh www-data -c \"{inner}\""
            return self.exec_tools(spec.namespace, pod, wrapped)

        self.exec_tools(
            spec.namespace,
            pod,
            """set -e
            mkdir -p /var/www/html/app/web/sites/default/files
            chown -R www-data:www-data /var/www/html/app/web/sites/default/files
            find /var/www/html/app/web/sites/default/files -type d -exec chmod 2775 {} \\;
            find /var/www/html/app/web/sites/default/files -type f -exec chmod 0664 {} \\;
            """,
        )

        status = run_www("cd /var/www/html/app && ./vendor/bin/drush status --fields=bootstrap --format=list 2>/dev/null || true")
        if "Successful" in status:
            log("Drupal ya instalado: ejecutando updb + cr")
            run_www("cd /var/www/html/app && ./vendor/bin/drush updb -y")
            run_www("cd /var/www/html/app && ./vendor/bin/drush cr")
        else:
            log("Drupal no instalado: ejecutando site-install")
            run_www(
                "cd /var/www/html/app && ./vendor/bin/drush site:install -y "
                f"--locale='{spec.locale}' --site-name='{spec.site_name}' "
                f"--account-name='{spec.admin_user}' --account-mail='{spec.admin_email}' "
                f"--account-pass='{spec.admin_password}'"
            )

        # Normaliza UI admin: navigation como módulo, claro/gin como temas.
        run_www("cd /var/www/html/app && ./vendor/bin/drush en -y navigation || true")
        run_www("cd /var/www/html/app && ./vendor/bin/drush theme:enable claro gin || true")
        run_www("cd /var/www/html/app && ./vendor/bin/drush cset -y system.theme admin claro || true")

        if spec.modules:
            enabled = run_www("cd /var/www/html/app && ./vendor/bin/drush pml --status=enabled --type=module --format=list")
            for module in spec.modules:
                if module in enabled.splitlines():
                    log(f"Módulo {module}: ya habilitado")
                    continue
                log(f"Módulo {module}: habilitando")
                run_www(f"cd /var/www/html/app && ./vendor/bin/drush en -y '{module}'")

        # Garantiza estado consistente tras site-install y cambios de módulos.
        run_www("cd /var/www/html/app && ./vendor/bin/drush cr")
        run_www("cd /var/www/html/app && ./vendor/bin/drush status")
        log("Bootstrap Drupal completado")

    def delete_namespace(self, namespace: str, log: LogFn) -> None:
        try:
            self.core.delete_namespace(namespace)
            log(f"Namespace {namespace} eliminado")
        except self._api_exception as exc:
            if exc.status == 404:
                log(f"Namespace {namespace} no existe (borrado idempotente)")
                return
            raise


class DrupalProvisioner:
    def __init__(
        self,
        k8s: K8sService,
        dns: DnsService,
        namespace_prefix: str,
        default_base_domain: str,
        default_admin_pass: str,
        dns_target: str,
        dns_ttl: int,
    ) -> None:
        self.k8s = k8s
        self.dns = dns
        self.namespace_prefix = namespace_prefix
        self.default_base_domain = default_base_domain
        self.default_admin_pass = default_admin_pass
        self.dns_target = dns_target
        self.dns_ttl = dns_ttl

    def build_spec(self, payload: dict[str, str], site_id: str) -> SiteSpec:
        slug = normalize_slug(payload.get("site_slug", ""))
        namespace = f"{self.namespace_prefix}{slug}"
        domain = (payload.get("domain") or "").strip()
        if not domain:
            domain = random_subdomain(self.default_base_domain)
        if not is_valid_domain(domain):
            raise ServiceError(f"Dominio inválido: {domain}")

        www_mode = payload.get("www_mode", "auto").strip()
        include = include_www(domain, www_mode)

        admin_user = payload.get("admin_user", "admin").strip() or "admin"
        admin_email = payload.get("admin_email", "").strip() or f"admin@{domain}"
        admin_password = self.default_admin_pass
        modules = [m for m in payload.get("modules", "redirect").split() if m]

        return SiteSpec(
            site_id=site_id,
            slug=slug,
            namespace=namespace,
            domain=domain,
            base_domain=self.default_base_domain,
            www_mode=www_mode,
            include_www=include,
            site_name=payload.get("site_name", "Mi Drupal").strip() or "Mi Drupal",
            locale=payload.get("locale", "es").strip() or "es",
            admin_user=admin_user,
            admin_email=admin_email,
            admin_password=admin_password,
            modules=modules,
        )

    def provision(self, spec: SiteSpec, log: LogFn) -> None:
        namespace_exists = self.k8s.namespace_exists(spec.namespace)
        creds: dict[str, str]
        if namespace_exists and self.k8s.secret_exists(spec.namespace, "drupalcms-secrets"):
            log("Namespace existente: reutilizando credenciales del secret")
            old = self.k8s.read_existing_credentials(spec.namespace)
            creds = {
                "mariadb_root_password": old["mariadb_root_password"],
                "mariadb_password": old["mariadb_password"],
                "drupal_hash_salt": old["drupal_hash_salt"],
            }
            spec.admin_user = old.get("drupal_admin_user") or spec.admin_user
            spec.admin_email = old.get("drupal_admin_email") or spec.admin_email
            spec.admin_password = old.get("drupal_admin_pass") or spec.admin_password
        else:
            creds = {
                "mariadb_root_password": random_hex(32),
                "mariadb_password": random_hex(32),
                "drupal_hash_salt": random_hex(64),
            }

        if not namespace_exists:
            self.dns.create_record(spec.domain, self.dns_target, self.dns_ttl, log)

        self.k8s.apply_resources(spec, creds, log)
        self.k8s.wait_ready(spec.namespace, log)
        self.k8s.bootstrap_drupal(spec, log)

    def delete_site(self, namespace: str, domain: str, log: LogFn) -> None:
        if not namespace.startswith(self.namespace_prefix):
            raise ServiceError(f"Namespace fuera de prefijo gestionado: {namespace}")
        self.k8s.delete_namespace(namespace, log)
        self.dns.delete_record(domain, self.dns_target, log)
