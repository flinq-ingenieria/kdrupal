# Drupal CMS App Image (GHCR)

Imagen Drupal CMS preconstruida para evitar `composer create-project` en cada despliegue.

## 1) Requisitos

- Cuenta GitHub
- Permiso para publicar paquetes en GHCR
- Token clásico o fine-grained con permisos:
  - `write:packages`
  - `read:packages`

## 2) Variables

```bash
export GH_USER="TU_USUARIO_GITHUB"
export GH_TOKEN="TU_TOKEN_GITHUB"
export IMAGE="ghcr.io/${GH_USER}/drupal-cms-app"
export TAG="2026-05-08.1"
```

## 3) Login en GHCR

```bash
echo "$GH_TOKEN" | docker login ghcr.io -u "$GH_USER" --password-stdin
```

## 4) Build y Push

Desde la raíz del repo:

```bash
docker build -t "$IMAGE:$TAG" -f images/drupal-cms-app/Dockerfile images/drupal-cms-app
docker push "$IMAGE:$TAG"
```

Opcional marcar `latest`:

```bash
docker tag "$IMAGE:$TAG" "$IMAGE:latest"
docker push "$IMAGE:latest"
```

## 5) Verificación

```bash
docker pull "$IMAGE:$TAG"
```

## 6) Cambios recomendados en el template de Kubernetes

Para usar esta imagen y acelerar arranque:

- Sustituir `image: drupal:11.3.8-php8.4-fpm-bookworm` por `image: ghcr.io/<org>/drupal-cms-app:<tag>` en `init-drupal-cms`, `php` y `tools`.
- Eliminar `apt-get`, `composer installer`, `composer create-project` en runtime (ya vienen en la imagen).
- Mantener en runtime solo:
  - creación de `settings.php`
  - bootstrap `drush`
  - permisos de `sites/default/files`

## 7) ¿Hay que cambiar profundamente el programa?

No de forma profunda. El backend Flask puede mantenerse casi igual.

Cambios mínimos en la app/template:

- Actualizar `drupal.template.yaml` para usar la imagen propia.
- Simplificar comandos del `initContainer`/`tools` para quitar instalación de paquetes y composer en caliente.
- (Opcional) Añadir variable de entorno `DRUPAL_APP_IMAGE` para versionar imagen sin tocar template.
