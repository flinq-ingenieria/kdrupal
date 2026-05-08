from __future__ import annotations

from dataclasses import dataclass

import pytest

from webapp.services import (
    DrupalProvisioner,
    DnsService,
    ServiceError,
    include_www,
    is_valid_domain,
    normalize_slug,
)


def test_normalize_slug():
    assert normalize_slug(" Cliente A ") == "cliente-a"
    assert normalize_slug("x__y") == "x-y"
    with pytest.raises(ServiceError):
        normalize_slug("---")


def test_include_www_modes():
    assert include_www("example.com", "auto") is True
    assert include_www("foo.example.com", "auto") is False
    assert include_www("foo.example.com", "with") is True
    assert include_www("foo.example.com", "without") is False
    with pytest.raises(ServiceError):
        include_www("example.com", "bad")


def test_domain_validation():
    assert is_valid_domain("example.com")
    assert is_valid_domain("a.b-c.com")
    assert not is_valid_domain("bad")


@dataclass
class FakeK8s:
    namespace_exists_value: bool = False

    def namespace_exists(self, namespace: str) -> bool:
        return self.namespace_exists_value

    def ensure_namespace(self, namespace: str) -> None:
        return None

    def ensure_image_pull_secret(self, namespace: str, secret_name: str, registry: str, username: str, password: str, email: str, log) -> None:
        log("pull secret ok")

    def secret_exists(self, namespace: str, name: str) -> bool:
        return False

    def apply_resources(self, spec, creds, log):
        log("apply")

    def wait_ready(self, namespace, log):
        log("ready")

    def bootstrap_drupal(self, spec, log):
        log("bootstrap")

    def delete_namespace(self, namespace, log):
        log(f"delete {namespace}")


@dataclass
class FakeDns:
    created: list
    deleted: list

    def create_record(self, fqdn, target, ttl, log):
        self.created.append((fqdn, target, ttl))
        log("dns create")

    def delete_record(self, fqdn, target, log):
        self.deleted.append((fqdn, target))
        log("dns delete")


def build_provisioner():
    dns = FakeDns(created=[], deleted=[])
    return DrupalProvisioner(
        k8s=FakeK8s(),
        dns=dns,
        namespace_prefix="drupal-",
        default_base_domain="example.com",
        default_admin_pass="FixedPass123!",
        drupal_app_image="ghcr.io/test/drupal-cms-app:test",
        image_pull_secret_name="ghcr-pull-secret",
        ghcr_registry="ghcr.io",
        ghcr_username="u",
        ghcr_token="t",
        ghcr_email="e@example.com",
        dns_target="203.0.113.10",
        dns_ttl=300,
    ), dns


def test_build_spec_generates_domain_if_missing():
    prov, _ = build_provisioner()
    spec = prov.build_spec(
        {
            "site_slug": "acme",
            "domain": "",
            "www_mode": "auto",
            "site_slug": "acme",
        },
        site_id="1",
    )
    assert spec.namespace == "drupal-acme"
    assert spec.domain.endswith(".example.com")
    assert spec.admin_password == "FixedPass123!"
    assert spec.admin_user == "pending-wizard"
    assert spec.drupal_app_image == "ghcr.io/test/drupal-cms-app:test"
    assert spec.image_pull_secret_name == "ghcr-pull-secret"


def test_provision_calls_dns_for_new_namespace():
    prov, dns = build_provisioner()
    spec = prov.build_spec({"site_slug": "acme", "domain": "acme.example.com"}, site_id="1")
    logs = []
    prov.provision(spec, logs.append)
    assert dns.created


def test_delete_blocks_non_prefixed_namespace():
    prov, _ = build_provisioner()
    with pytest.raises(ServiceError):
        prov.delete_site("other-acme", "acme.example.com", lambda _: None)


def test_dns_service_mock(monkeypatch):
    calls = {}

    class Resp:
        status_code = 200
        text = '{"success":true}'

        def json(self):
            return {"success": True}

    def fake_post(url, params, data, timeout):
        calls["url"] = url
        calls["params"] = params
        calls["data"] = data
        calls["timeout"] = timeout
        return Resp()

    monkeypatch.setattr("requests.post", fake_post)
    dns = DnsService("https://api.example.com", "user", "pwd", "example.com")
    dns.create_record("a.example.com", "1.2.3.4", 300, lambda _: None)
    assert calls["params"]["command"] == "Domain_Zone_AddTypeA"
    assert calls["params"]["domain"] == "example.com"
    assert calls["params"]["hostname"] == "a"
    assert calls["data"]["AUTH_USER"] == "user"
