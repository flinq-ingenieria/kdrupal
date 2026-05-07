#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TEMPLATE="$SCRIPT_DIR/drupal.template.yaml"

for cmd in kubectl envsubst openssl; do
  command -v "$cmd" >/dev/null 2>&1 || { echo "Error: '$cmd' no encontrado en PATH"; exit 1; }
done

[ -f "$TEMPLATE" ] || { echo "Error: template no encontrado en $TEMPLATE"; exit 1; }

echo "=== Despliegue de Drupal CMS en Kubernetes ==="
echo ""

read -rp "Dominio (ej: example.com): " DOMAIN
read -rp "Namespace de Kubernetes:   " NAMESPACE

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

# Detectar si es una reinstalación sobre namespace existente
NAMESPACE_EXISTS=false
if kubectl get namespace "$NAMESPACE" >/dev/null 2>&1; then
  NAMESPACE_EXISTS=true
  echo ""
  echo "AVISO: el namespace '$NAMESPACE' ya existe."
  echo "  Si hay una instalación activa, regenerar el Secret romperá la BD."
  read -rp "  ¿Continuar igualmente? [s/N] " override
  case "$override" in
    [sS]|[yY]) ;;
    *) echo "Cancelado."; exit 0 ;;
  esac
fi

# Generar credenciales
MARIADB_ROOT_PASSWORD=$(openssl rand -hex 16)
MARIADB_PASSWORD=$(openssl rand -hex 16)
DRUPAL_HASH_SALT=$(openssl rand -hex 32)

# Valores derivados
TLS_SECRET_NAME="$(echo "$DOMAIN" | tr '.' '-')-tls"

# Escapar puntos del dominio para regex PHP (pasará por heredoc de bash en el pod:
# \\\\ en sed → \\ en DOMAIN_REGEX → \\ en YAML → \ en el heredoc del pod → \ en PHP)
DOMAIN_REGEX=$(echo "$DOMAIN" | sed 's/\./\\\\./g')

export NAMESPACE DOMAIN MARIADB_ROOT_PASSWORD MARIADB_PASSWORD DRUPAL_HASH_SALT TLS_SECRET_NAME DOMAIN_REGEX

echo ""
echo "--- Resumen del despliegue ---"
printf "  Dominio:     %s\n"  "$DOMAIN"
printf "  Namespace:   %s\n"  "$NAMESPACE"
printf "  TLS Secret:  %s\n"  "$TLS_SECRET_NAME"
echo ""
read -rp "¿Aplicar al cluster? [s/N] " confirm

case "$confirm" in
  [sS]|[yY]) ;;
  *) echo "Cancelado."; exit 0 ;;
esac

echo ""
echo "Aplicando manifiestos..."

# envsubst con lista explícita para no tocar variables de nginx ($uri, $query_string...)
# ni las del pod ($DB_NAME, $DB_USER, $DB_PASSWORD, $MARIADB_ROOT_PASSWORD en probes)
envsubst '${NAMESPACE} ${DOMAIN} ${MARIADB_ROOT_PASSWORD} ${MARIADB_PASSWORD} ${DRUPAL_HASH_SALT} ${TLS_SECRET_NAME} ${DOMAIN_REGEX}' \
  < "$TEMPLATE" | kubectl apply -f -

echo ""
echo "Despliegue enviado. Monitoriza el estado con:"
echo "  kubectl -n $NAMESPACE get pods -w"
echo ""
echo "El initContainer tarda ~5 min (descarga Composer y crea el proyecto)."
echo "Cuando los pods estén Running, completa la instalación de Drupal:"
echo "  kubectl -n $NAMESPACE exec -it deploy/drupalcms -c tools -- \\"
echo "    drush site-install --yes --site-name='Mi Drupal' --account-name=admin --account-pass=CAMBIA_ESTO"
