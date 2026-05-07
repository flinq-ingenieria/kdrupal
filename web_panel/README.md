# Drupal Cluster Manager

Aplicación web Flask para crear/listar/eliminar sitios Drupal en Kubernetes sin usar script bash en runtime.

## Variables de entorno

- `PANEL_AUTH_TOKEN` (obligatoria)
- `DEFAULT_BASE_DOMAIN` (obligatoria, usada para autogenerar dominio)
- `NAMESPACE_PREFIX` (opcional, por defecto `drupal-`)
- `SQLITE_PATH` (opcional, por defecto `web_panel/data/panel.db`)
- `DINAHOSTING_API_URL` (obligatoria)
- `DINAHOSTING_AUTH_USER` (obligatoria)
- `DINAHOSTING_AUTH_PWD` (obligatoria)
- `DINAHOSTING_DNS_TARGET` (obligatoria, IP/CNAME objetivo)
- `DINAHOSTING_DNS_TTL` (opcional, por defecto `300`)

## Arranque

```bash
cd web_panel
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
export PANEL_AUTH_TOKEN='cambia-este-token'
export DEFAULT_BASE_DOMAIN='tudominio.com'
export DINAHOSTING_API_URL='https://dinahosting.com/special/api.php'
export DINAHOSTING_AUTH_USER='tu_usuario_dinahosting'
export DINAHOSTING_AUTH_PWD='tu_password_dinahosting'
export DINAHOSTING_DNS_TARGET='203.0.113.10'
python app.py
```

Abrir:

`http://localhost:8080/?auth_token=cambia-este-token`

## Endpoints

- `GET /` dashboard (crear/listar/eliminar)
- `POST /sites` crear sitio
- `POST /sites/<site_id>/delete` eliminar sitio completo (confirmación por slug)
- `GET /jobs/<job_id>` estado y logs
