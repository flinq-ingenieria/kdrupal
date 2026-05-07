#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TEMPLATE="$SCRIPT_DIR/drupal.template.yaml"

for cmd in kubectl envsubst openssl; do
  command -v "$cmd" >/dev/null 2>&1 || { echo "Error: '$cmd' no encontrado en PATH"; exit 1; }
done

[ -f "$TEMPLATE" ] || { echo "Error: template no encontrado en $TEMPLATE"; exit 1; }

CURRENT_STAGE="inicio"
on_error() {
  local exit_code=$?
  echo ""
  echo "Error en etapa '$CURRENT_STAGE' (exit code: $exit_code)."
  echo "Revisa eventos y pods con: kubectl -n ${NAMESPACE:-<namespace>} get pods,events"
}
trap on_error ERR

read_input_with_default() {
  local prompt="$1"
  local default_value="$2"
  local result

  if [ -t 0 ]; then
    read -rp "$prompt [$default_value]: " result
    result="${result:-$default_value}"
  else
    result="$default_value"
  fi

  printf '%s' "$result"
}

is_true() {
  case "${1:-}" in
    1|true|TRUE|True|yes|YES|Yes|y|Y|si|SI|Si|s|S) return 0 ;;
    *) return 1 ;;
  esac
}

echo "=== Despliegue de Drupal CMS en Kubernetes ==="
echo ""

DOMAIN="${DOMAIN:-}"
NAMESPACE="${NAMESPACE:-}"
if [ -z "$DOMAIN" ]; then
  read -rp "Dominio (ej: example.com): " DOMAIN
fi
if [ -z "$NAMESPACE" ]; then
  read -rp "Namespace de Kubernetes:   " NAMESPACE
fi

# Validar dominio
if ! echo "$DOMAIN" | grep -qE '^[a-zA-Z0-9][a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'; then
  echo "Error: dominio inválido '$DOMAIN'"
  exit 1
fi

# Validar namespace (RFC 1123 DNS label)
if ! echo "$NAMESPACE" | grep -qE '^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$'; then
  echo "Error: namespace inválido '$NAMESPACE' (minúsculas, alfanumérico y guiones)"
  exit 1
fi

DEPLOY_TIMEOUT_SECONDS="${DEPLOY_TIMEOUT_SECONDS:-900}"
DRUPAL_SITE_NAME="${DRUPAL_SITE_NAME:-Mi Drupal}"
DRUPAL_ADMIN_USER="${DRUPAL_ADMIN_USER:-admin}"
DRUPAL_ADMIN_EMAIL_DEFAULT="admin@${DOMAIN}"
DRUPAL_ADMIN_EMAIL="${DRUPAL_ADMIN_EMAIL:-$DRUPAL_ADMIN_EMAIL_DEFAULT}"
DRUPAL_ENABLE_MODULES="${DRUPAL_ENABLE_MODULES:-redirect}"
AUTO_CONFIRM="${AUTO_CONFIRM:-false}"

if [ -z "${DRUPAL_ADMIN_PASS:-}" ]; then
  DRUPAL_ADMIN_PASS="$(openssl rand -base64 24 | tr -d '\n' | tr '/+' 'Aa' | cut -c1-24)"
else
  DRUPAL_ADMIN_PASS="${DRUPAL_ADMIN_PASS}"
fi

# Detectar si es una reinstalación sobre namespace existente
NAMESPACE_EXISTS=false
if kubectl get namespace "$NAMESPACE" >/dev/null 2>&1; then
  NAMESPACE_EXISTS=true
  echo ""
  echo "AVISO: el namespace '$NAMESPACE' ya existe."
  echo "  Si hay una instalación activa, regenerar credenciales de BD romperá la conexión."

  if is_true "$AUTO_CONFIRM"; then
    echo "  AUTO_CONFIRM activo: se continúa sin prompt."
  else
    read -rp "  ¿Continuar igualmente? [s/N] " override
    case "$override" in
      [sS]|[yY]) ;;
      *) echo "Cancelado."; exit 0 ;;
    esac
  fi
fi

# Generar credenciales (solo para namespace nuevo; en existente reutilizar)
if [ "$NAMESPACE_EXISTS" = true ] && kubectl -n "$NAMESPACE" get secret drupalcms-secrets >/dev/null 2>&1; then
  echo ""
  echo "Reutilizando credenciales existentes del Secret drupalcms-secrets."
  MARIADB_ROOT_PASSWORD="$(kubectl -n "$NAMESPACE" get secret drupalcms-secrets -o jsonpath='{.data.mariadb-root-password}' | base64 -d)"
  MARIADB_PASSWORD="$(kubectl -n "$NAMESPACE" get secret drupalcms-secrets -o jsonpath='{.data.mariadb-password}' | base64 -d)"
  DRUPAL_HASH_SALT="$(kubectl -n "$NAMESPACE" get secret drupalcms-secrets -o jsonpath='{.data.drupal-hash-salt}' | base64 -d)"

  EXISTING_ADMIN_USER="$(kubectl -n "$NAMESPACE" get secret drupalcms-secrets -o jsonpath='{.data.drupal-admin-user}' 2>/dev/null | base64 -d || true)"
  EXISTING_ADMIN_EMAIL="$(kubectl -n "$NAMESPACE" get secret drupalcms-secrets -o jsonpath='{.data.drupal-admin-email}' 2>/dev/null | base64 -d || true)"
  EXISTING_ADMIN_PASS="$(kubectl -n "$NAMESPACE" get secret drupalcms-secrets -o jsonpath='{.data.drupal-admin-pass}' 2>/dev/null | base64 -d || true)"

  if [ -n "$EXISTING_ADMIN_USER" ]; then DRUPAL_ADMIN_USER="$EXISTING_ADMIN_USER"; fi
  if [ -n "$EXISTING_ADMIN_EMAIL" ]; then DRUPAL_ADMIN_EMAIL="$EXISTING_ADMIN_EMAIL"; fi
  if [ -n "$EXISTING_ADMIN_PASS" ]; then DRUPAL_ADMIN_PASS="$EXISTING_ADMIN_PASS"; fi
else
  MARIADB_ROOT_PASSWORD="$(openssl rand -hex 16)"
  MARIADB_PASSWORD="$(openssl rand -hex 16)"
  DRUPAL_HASH_SALT="$(openssl rand -hex 32)"
fi

# Valores derivados
TLS_SECRET_NAME="$(echo "$DOMAIN" | tr '.' '-')-tls"

# Escapar puntos del dominio para regex PHP
DOMAIN_REGEX="$(echo "$DOMAIN" | sed 's/\./\\\\./g')"

if [ -t 0 ]; then
  DRUPAL_SITE_NAME="$(read_input_with_default "Nombre del sitio Drupal" "$DRUPAL_SITE_NAME")"
  DRUPAL_ADMIN_USER="$(read_input_with_default "Usuario admin Drupal" "$DRUPAL_ADMIN_USER")"
  DRUPAL_ADMIN_EMAIL="$(read_input_with_default "Email admin Drupal" "$DRUPAL_ADMIN_EMAIL")"
  DRUPAL_ENABLE_MODULES="$(read_input_with_default "Módulos a habilitar (separados por espacio)" "$DRUPAL_ENABLE_MODULES")"
fi

export NAMESPACE DOMAIN MARIADB_ROOT_PASSWORD MARIADB_PASSWORD DRUPAL_HASH_SALT TLS_SECRET_NAME DOMAIN_REGEX DRUPAL_ADMIN_USER DRUPAL_ADMIN_PASS DRUPAL_ADMIN_EMAIL

echo ""
echo "--- Resumen del despliegue ---"
printf "  Dominio:          %s\n" "$DOMAIN"
printf "  Namespace:        %s\n" "$NAMESPACE"
printf "  TLS Secret:       %s\n" "$TLS_SECRET_NAME"
printf "  Site name:        %s\n" "$DRUPAL_SITE_NAME"
printf "  Admin user:       %s\n" "$DRUPAL_ADMIN_USER"
printf "  Admin email:      %s\n" "$DRUPAL_ADMIN_EMAIL"
printf "  Módulos:          %s\n" "$DRUPAL_ENABLE_MODULES"
printf "  Timeout rollout:  %ss\n" "$DEPLOY_TIMEOUT_SECONDS"
echo ""

if is_true "$AUTO_CONFIRM"; then
  echo "AUTO_CONFIRM activo: aplicando cambios sin prompt."
else
  read -rp "¿Aplicar al cluster? [s/N] " confirm
  case "$confirm" in
    [sS]|[yY]) ;;
    *) echo "Cancelado."; exit 0 ;;
  esac
fi

CURRENT_STAGE="apply-manifests"
echo ""
echo "Aplicando manifiestos..."

# envsubst con lista explícita para no tocar variables de nginx ($uri, $query_string...)
envsubst '${NAMESPACE} ${DOMAIN} ${MARIADB_ROOT_PASSWORD} ${MARIADB_PASSWORD} ${DRUPAL_HASH_SALT} ${TLS_SECRET_NAME} ${DOMAIN_REGEX} ${DRUPAL_ADMIN_USER} ${DRUPAL_ADMIN_PASS} ${DRUPAL_ADMIN_EMAIL}' \
  < "$TEMPLATE" | kubectl apply -f -

CURRENT_STAGE="wait-rollouts"
echo ""
echo "Esperando recursos listos..."
kubectl -n "$NAMESPACE" rollout status statefulset/mariadb --timeout="${DEPLOY_TIMEOUT_SECONDS}s"
kubectl -n "$NAMESPACE" rollout status deployment/drupalcms --timeout="${DEPLOY_TIMEOUT_SECONDS}s"
kubectl -n "$NAMESPACE" wait --for=condition=ready pod -l app=drupalcms --timeout="${DEPLOY_TIMEOUT_SECONDS}s"

CURRENT_STAGE="drush-bootstrap"
echo ""
echo "Ejecutando bootstrap idempotente de Drupal..."

TOOLS_POD="$(kubectl -n "$NAMESPACE" get pod -l app=drupalcms -o jsonpath='{.items[0].metadata.name}')"
[ -n "$TOOLS_POD" ] || { echo "Error: no se encontró pod de drupalcms"; exit 1; }

if kubectl -n "$NAMESPACE" exec "$TOOLS_POD" -c tools -- sh -lc "cd /var/www/html/app && ./vendor/bin/drush status --fields=bootstrap --format=list 2>/dev/null | grep -qi 'Successful'"; then
  echo "Drupal ya instalado. Ejecutando tareas de mantenimiento seguras..."
  kubectl -n "$NAMESPACE" exec "$TOOLS_POD" -c tools -- sh -lc "cd /var/www/html/app && ./vendor/bin/drush updb -y && ./vendor/bin/drush cr"
else
  echo "Drupal no instalado. Ejecutando site-install..."
  kubectl -n "$NAMESPACE" exec "$TOOLS_POD" -c tools -- sh -lc "cd /var/www/html/app && ./vendor/bin/drush site:install -y --site-name='$DRUPAL_SITE_NAME' --account-name='$DRUPAL_ADMIN_USER' --account-mail='$DRUPAL_ADMIN_EMAIL' --account-pass='$DRUPAL_ADMIN_PASS'"
fi

if [ -n "${DRUPAL_ENABLE_MODULES// /}" ]; then
  echo ""
  echo "Habilitando módulos solicitados..."
  read -r -a MODULES <<< "$DRUPAL_ENABLE_MODULES"
  for module in "${MODULES[@]}"; do
    [ -n "$module" ] || continue
    if kubectl -n "$NAMESPACE" exec "$TOOLS_POD" -c tools -- sh -lc "cd /var/www/html/app && ./vendor/bin/drush pml --status=enabled --type=module --no-core --format=list | grep -Fxq '$module'"; then
      echo "  - $module: ya estaba habilitado"
    else
      echo "  - $module: habilitando"
      kubectl -n "$NAMESPACE" exec "$TOOLS_POD" -c tools -- sh -lc "cd /var/www/html/app && ./vendor/bin/drush en -y '$module'"
    fi
  done
fi

CURRENT_STAGE="final-verification"
echo ""
echo "Verificación final..."
kubectl -n "$NAMESPACE" rollout status statefulset/mariadb --timeout="${DEPLOY_TIMEOUT_SECONDS}s" >/dev/null
kubectl -n "$NAMESPACE" rollout status deployment/drupalcms --timeout="${DEPLOY_TIMEOUT_SECONDS}s" >/dev/null
kubectl -n "$NAMESPACE" exec "$TOOLS_POD" -c tools -- sh -lc "cd /var/www/html/app && ./vendor/bin/drush status"

if echo "$DRUPAL_ENABLE_MODULES" | tr ' ' '\n' | grep -Fxq "redirect"; then
  kubectl -n "$NAMESPACE" exec "$TOOLS_POD" -c tools -- sh -lc "cd /var/www/html/app && ./vendor/bin/drush pml --status=enabled --format=list | grep -Fx redirect"
fi

CURRENT_STAGE="completed"
echo ""
echo "Despliegue y bootstrap completados."
echo ""
echo "--- Credenciales Drupal admin ---"
printf "  URL:      https://%s\n" "$DOMAIN"
printf "  Usuario:  %s\n" "$DRUPAL_ADMIN_USER"
printf "  Password: %s\n" "$DRUPAL_ADMIN_PASS"
printf "  Email:    %s\n" "$DRUPAL_ADMIN_EMAIL"
