"""Obtencion y refresh de la base APOC de ARCA hacia la tabla local.

Fuente verificada (2026-07-20, ver runbook): descarga publica SIN
autenticacion en ``DownloadFile.aspx`` -> ``FacturasApocrifas.zip`` con un
``FacturasApocrifas.txt`` (~45.000 CUITs). Formato observado:

    # AFIP - Facturas Apocrifas
    # Generado - 20/7/2026
    # Estructura del Archivo: CUIT, Fecha Condicion Apocrifo, Fecha Publicacion, Descripcion
    30703948983,08/11/2005,08/11/2005,,

La red se toca UNICAMENTE en el job de refresh (cron / Cloud Scheduler via
``python -m ap_control_tower.controls.arca.refresh_apoc``); el control
C11_APOC por documento es siempre lookup local. Cada refresh queda versionado
con fecha, checksum SHA-256 y cantidad de registros; si el checksum coincide
con la ultima version, el refresh es un no-op idempotente.
"""

from __future__ import annotations

import hashlib
import io
import zipfile
from datetime import datetime, timezone

DEFAULT_URL = ("https://servicioscf.afip.gob.ar/Facturacion/"
               "facturasApocrifas/DownloadFile.aspx")
DOWNLOAD_TIMEOUT_SECONDS = 120
# Antiguedad maxima de la base antes de la advertencia global visible.
APOC_STALE_DIAS = 15


# ------------------------------------------------------------------ parsing
def extract_txt(raw: bytes) -> bytes:
    """Contenido de texto de la descarga: acepta el ZIP oficial o el TXT plano."""
    if raw[:4] == b"PK\x03\x04":
        with zipfile.ZipFile(io.BytesIO(raw)) as bundle:
            names = bundle.namelist()
            if not names:
                raise ValueError("ZIP de APOC vacio")
            return bundle.read(names[0])
    return raw


def parse_apoc_text(text: str) -> tuple[list[tuple[str, str]], int]:
    """Filas (cuit, fuente) de la base + cantidad de lineas descartadas.

    ``fuente`` conserva las fechas de condicion/publicacion y la descripcion
    tal como vienen (truncado a 255). No se valida el digito verificador: la
    base historica de ARCA contiene CUITs legados y manda la fuente oficial.
    """
    entradas: dict[str, str] = {}
    descartadas = 0
    for linea in text.splitlines():
        linea = linea.strip().rstrip("\t").strip()
        if not linea or linea.startswith("#"):
            continue
        partes = [p.strip() for p in linea.split(",")]
        cuit = partes[0]
        if not (len(cuit) == 11 and cuit.isdigit()):
            descartadas += 1
            continue
        detalle = [d for d in (
            f"cond={partes[1]}" if len(partes) > 1 and partes[1] else "",
            f"pub={partes[2]}" if len(partes) > 2 and partes[2] else "",
            f"desc={partes[3]}" if len(partes) > 3 and partes[3] else "",
        ) if d]
        entradas[cuit] = "|".join(detalle)[:255]
    return list(entradas.items()), descartadas


def download(url: str = DEFAULT_URL,
             timeout: int = DOWNLOAD_TIMEOUT_SECONDS) -> bytes:
    """Descarga la base publica. SOLO para el job de refresh, jamas en el
    camino critico por documento."""
    from urllib.request import Request, urlopen

    request = Request(url, headers={"User-Agent": "ap-control-tower-refresh-apoc"})
    with urlopen(request, timeout=timeout) as response:  # nosec: URL oficial fija
        return response.read()


# ------------------------------------------------------------------ refresh
def refresh_from_bytes(db, raw: bytes, origen: str) -> dict:
    """Importa una descarga a las tablas locales. Idempotente por checksum.

    Devuelve un resumen dict: accion (importada|sin_cambios), version_id,
    checksum, cantidad_registros, descartadas.
    """
    from sqlalchemy import delete, select

    from ...persistence.models_sql import ArcaApocEntry, ArcaApocVersion

    contenido = extract_txt(raw)
    checksum = hashlib.sha256(contenido).hexdigest()
    ultima = db.execute(
        select(ArcaApocVersion).order_by(ArcaApocVersion.id.desc()).limit(1)
    ).scalar_one_or_none()
    if ultima is not None and ultima.checksum == checksum:
        return {"accion": "sin_cambios", "version_id": ultima.id,
                "checksum": checksum,
                "cantidad_registros": ultima.cantidad_registros,
                "descartadas": 0}

    entradas, descartadas = parse_apoc_text(contenido.decode("utf-8", "replace"))
    if not entradas:
        raise ValueError("La descarga de APOC no contiene CUITs: no se importa "
                         "(se conserva la base local vigente)")
    version = ArcaApocVersion(
        fecha_descarga=datetime.now(timezone.utc),
        checksum=checksum, cantidad_registros=len(entradas),
        origen=origen[:255])
    db.add(version)
    db.flush()  # asigna version.id
    db.execute(delete(ArcaApocEntry))
    db.add_all([ArcaApocEntry(cuit=cuit, fuente=fuente or None,
                              version_id=version.id)
                for cuit, fuente in entradas])
    return {"accion": "importada", "version_id": version.id,
            "checksum": checksum, "cantidad_registros": len(entradas),
            "descartadas": descartadas}


# ------------------------------------------------------------------ lookups
def latest_version_info(db) -> dict | None:
    """Version vigente de la base local, con su antiguedad en dias."""
    from sqlalchemy import select

    from ...persistence.models_sql import ArcaApocVersion

    version = db.execute(
        select(ArcaApocVersion).order_by(ArcaApocVersion.id.desc()).limit(1)
    ).scalar_one_or_none()
    if version is None:
        return None
    descargada = version.fecha_descarga
    if descargada.tzinfo is None:
        descargada = descargada.replace(tzinfo=timezone.utc)
    antiguedad = (datetime.now(timezone.utc) - descargada).days
    return {"version_id": version.id, "checksum": version.checksum,
            "cantidad_registros": version.cantidad_registros,
            "origen": version.origen,
            "fecha_descarga": descargada.isoformat(timespec="seconds"),
            "antiguedad_dias": antiguedad,
            "desactualizada": antiguedad > APOC_STALE_DIAS}


def is_listed(db, cuit) -> bool:
    """True si el CUIT figura en la base APOC local vigente."""
    from sqlalchemy import select

    from ...persistence.models_sql import ArcaApocEntry
    from .cuit import normalizar

    limpio = normalizar(cuit)
    if limpio is None:
        return False
    return db.execute(
        select(ArcaApocEntry.cuit).where(ArcaApocEntry.cuit == limpio)
    ).scalar_one_or_none() is not None
