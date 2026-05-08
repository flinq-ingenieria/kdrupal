# Drupal Cluster Manager

Aplicación web Flask para crear/listar/eliminar sitios Drupal en Kubernetes sin usar script bash en runtime.

## Variables de entorno

- `PANEL_AUTH_TOKEN` (obligatoria)
- `DEFAULT_BASE_DOMAIN` (obligatoria, usada para autogenerar dominio)
- `DEFAULT_ADMIN_PASS` (obligatoria, password fijo del admin de Drupal)
- `NAMESPACE_PREFIX` (opcional, por defecto `drupal-`)
- `DRUPAL_APP_IMAGE` (obligatoria, imagen preconstruida con Drupal CMS)
- `SQLITE_PATH` (opcional, por defecto `web_panel/data/panel.db`)
- `DINAHOSTING_API_URL` (obligatoria)
- `DINAHOSTING_AUTH_USER` (obligatoria)
- `DINAHOSTING_AUTH_PWD` (obligatoria)
- `DINAHOSTING_DNS_TARGET` (obligatoria, IP/CNAME objetivo)
- `DINAHOSTING_DNS_TTL` (opcional, por defecto `300`)
- `GHCR_EMAIL` (opcional)
- `GHCR_TOKEN` (obligatoria, token GitHub `read:packages`)
- `GHCR_USERNAME` (obligatoria, para crear imagePullSecret)
- `GHCR_REGISTRY` (opcional, por defecto `ghcr.io`)
- `IMAGE_PULL_SECRET_NAME` (opcional, por defecto `ghcr-pull-secret`)

## Arranque

```bash
cd web_panel
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.sample .env
# Edita .env con tus valores
python app.py
```

Abrir:

`http://localhost:8080/?auth_token=cambia-este-token`

## Endpoints

- `GET /` dashboard (crear/listar/eliminar)
- `POST /sites` crear sitio
- `POST /sites/<site_id>/delete` eliminar sitio completo (confirmación por slug)
- `GET /jobs/<job_id>` estado y logs


La app carga automáticamente `web_panel/.env` si existe.


## Imagen propia (GHCR)

Construye y publica una imagen Drupal CMS preconfigurada (manual) y úsala en `DRUPAL_APP_IMAGE`.

```bash
export GH_USER="TU_USUARIO_GITHUB"
export GH_TOKEN="TU_TOKEN_GITHUB"
export IMAGE="ghcr.io/${GH_USER}/drupal-cms-app"
export TAG="2026-05-08.1"

echo "$GH_TOKEN" | docker login ghcr.io -u "$GH_USER" --password-stdin
docker build -t "$IMAGE:$TAG" -f images/drupal-cms-app/Dockerfile images/drupal-cms-app
docker push "$IMAGE:$TAG"
```

En `web_panel/.env`:

```env
DRUPAL_APP_IMAGE=ghcr.io/<tu-org>/drupal-cms-app:2026-05-08.1
```

Prueba completa:

```bash
cd web_panel
cp .env.sample .env
# editar .env (token, dinahosting, DRUPAL_APP_IMAGE, etc.)
python app.py
```

Luego crea un sitio desde la UI y valida que en Kubernetes los contenedores `init-drupal-cms`, `php` y `tools` usan tu imagen de GHCR.


La app crea/actualiza automáticamente un `imagePullSecret` por namespace usando `GHCR_*` e `IMAGE_PULL_SECRET_NAME`.


El flujo de creación deja Drupal limpio y listo para **instalación web manual** (wizard en `/`).
