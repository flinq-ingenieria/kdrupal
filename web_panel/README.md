# Drupal Cluster Web Panel

Panel web simple para lanzar el flujo de `deploy-drupal.sh` y ver estado/logs de ejecución.

## Requisitos

- Python 3.11+
- Acceso local a `kubectl` y contexto configurado
- El script `deploy-drupal.sh` en la raíz del repo
- `PANEL_AUTH_TOKEN` configurado (obligatorio)

## Arranque

```bash
cd web_panel
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
export PANEL_AUTH_TOKEN='cambia-este-token'
python app.py
```

Abre `http://localhost:8080/?auth_token=cambia-este-token`.

También puedes autenticar con cabecera `Authorization: Bearer <token>` o `X-Auth-Token: <token>`.

## Notas

- El proceso mantiene los jobs en memoria; al reiniciar la app se pierden historiales.
- Usa `AUTO_CONFIRM=true` para evitar prompts interactivos del script.
- Si usas `base_domain` y no `domain`, el script intentará provisión DNS y ahora pasa `DNS_AUTH_TOKEN` como segundo argumento a `provision_dns_record_placeholder(fqdn, token, target, ttl)`.
