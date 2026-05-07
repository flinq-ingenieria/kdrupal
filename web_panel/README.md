# Drupal Cluster Web Panel

Panel web simple para lanzar el flujo de `deploy-drupal.sh` y ver estado/logs de ejecución.

## Requisitos

- Python 3.11+
- Acceso local a `kubectl` y contexto configurado
- El script `deploy-drupal.sh` en la raíz del repo

## Arranque

```bash
cd web_panel
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python app.py
```

Abre `http://localhost:8080`.

## Notas

- El proceso mantiene los jobs en memoria; al reiniciar la app se pierden historiales.
- Usa `AUTO_CONFIRM=true` para evitar prompts interactivos del script.
- Si usas `base_domain` y no `domain`, el script intentará provisión DNS con `provision_dns_record_placeholder`, que actualmente falla por diseño.
