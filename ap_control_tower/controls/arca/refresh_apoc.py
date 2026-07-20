"""Job de refresh de la base APOC local.

Uso (cron / Cloud Scheduler; el scheduler NO se gestiona desde aca):

    AP_DATABASE_URL=postgresql+psycopg://... \
        python -m ap_control_tower.controls.arca.refresh_apoc

Opciones:
    --url URL      fuente a descargar (default: descarga publica oficial)
    --file RUTA    importa un archivo local (zip o txt) en vez de descargar

Exit code 0 = base importada o sin cambios; 1 = error (la base local vigente
se conserva intacta ante cualquier falla). Frecuencia recomendada: diaria
(ARCA regenera el archivo a diario); la advertencia de base desactualizada
salta a los 15 dias.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default=None,
                        help="URL de descarga (default: oficial publica)")
    parser.add_argument("--file", type=Path, default=None,
                        help="archivo local zip/txt en lugar de descargar")
    args = parser.parse_args(argv)

    from ...persistence.config import is_persistence_configured
    from . import apoc_source

    if not is_persistence_configured():
        print("ERROR: falta AP_DATABASE_URL / DATABASE_URL; el refresh APOC "
              "necesita la base local (ver runbook_controles_arca.md)")
        return 1

    if args.file is not None:
        raw = args.file.read_bytes()
        origen = f"archivo:{args.file.name}"
    else:
        url = args.url or apoc_source.DEFAULT_URL
        print(f"Descargando base APOC desde {url} ...")
        raw = apoc_source.download(url)
        origen = url

    from ...persistence.session import build_engine, session_scope

    try:
        with session_scope(build_engine()) as db:
            resumen = apoc_source.refresh_from_bytes(db, raw, origen=origen)
    except Exception as exc:  # la base vigente queda intacta (rollback)
        print(f"ERROR: refresh APOC fallido: {exc}")
        return 1

    print("Refresh APOC:", resumen["accion"])
    print(f"  version_id={resumen['version_id']}  "
          f"registros={resumen['cantidad_registros']}  "
          f"descartadas={resumen['descartadas']}")
    print(f"  checksum={resumen['checksum']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
