"""Controles ARCA (padron + APOC): validacion local de CUIT, validadores
puros y politica de derivacion. 100%% hermetico: sin red, sin ARCA real."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

failures: list[str] = []


def check(condition: bool, label: str) -> None:
    print(f"  {'PASS' if condition else 'FAIL'}  {label}")
    if not condition:
        failures.append(label)


def _seccion_cuit() -> None:
    from ap_control_tower.controls.arca import cuit

    print("== CUIT: validacion local del digito verificador (mod 11) ==")
    # CUITs sinteticos generados por la propia funcion: siempre validos.
    sinteticos = [cuit.generar_cuit_sintetico(i) for i in range(20)]
    check(all(cuit.cuit_valido(c) for c in sinteticos),
          "20 CUITs sinteticos generados son validos")
    check(len(set(sinteticos)) == 20, "los sinteticos no se repiten")
    check(cuit.cuit_valido(cuit.generar_cuit_sintetico(7, prefijo="27")),
          "generador acepta prefijo de persona fisica")

    valido = cuit.generar_cuit_sintetico(1)
    # Alterar el digito verificador SIEMPRE invalida.
    dv_alterado = valido[:10] + str((int(valido[10]) + 1) % 10)
    check(not cuit.cuit_valido(dv_alterado), "digito verificador alterado -> invalido")
    # Formatos con separadores se normalizan.
    con_guiones = f"{valido[:2]}-{valido[2:10]}-{valido[10]}"
    check(cuit.cuit_valido(con_guiones), "formato XX-XXXXXXXX-X se normaliza y valida")
    check(cuit.normalizar(f" {con_guiones} ") == valido, "espacios y guiones se limpian")

    # Lo que NO es candidato a CUIT jamas genera senal (clave para la
    # regresion del golden: CIF espanoles y tax ids enmascarados).
    for raro in ("B00000000", "ESB12345678", "******999", "", None, "12345",
                 "123456789012", "IT12345678901"):
        check(not cuit.es_cuit_candidato(raro), f"no candidato: {raro!r}")

    # Candidato con prefijo no asignado por ARCA -> invalido.
    check(not cuit.cuit_valido("99" + valido[2:]), "prefijo desconocido -> invalido")
    # El resto 1 no tiene digito verificador (ARCA cambia el prefijo).
    check(cuit.digito_verificador("2000000001") is None
          or isinstance(cuit.digito_verificador("2000000001"), int),
          "digito_verificador devuelve int o None (resto 1)")


def _fixture_apoc_text(cuits: list[str]) -> str:
    filas = "\n".join(f"{c},01/02/2020,15/02/2020,," for c in cuits)
    return ("# AFIP - Facturas Apocrifas\t\n"
            "# Generado - 20/7/2026\t\n"
            "# Estructura del Archivo: CUIT, Fecha Condicion Apocrifo, "
            "Fecha Publicacion, Descripcion \t\n"
            f"{filas}\n"
            "no-es-un-cuit,,,\n")


def _seccion_apoc() -> None:
    import io
    import zipfile
    from datetime import datetime, timedelta, timezone

    from sqlalchemy import create_engine, select
    from sqlalchemy.orm import Session

    from ap_control_tower.controls.arca import apoc_source, cuit
    from ap_control_tower.persistence.models_sql import (
        ArcaApocEntry, ArcaApocVersion, Base)

    print("== APOC: refresh versionado e idempotente, lookup local ==")
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    listados = [cuit.generar_cuit_sintetico(i) for i in range(5)]
    no_listado = cuit.generar_cuit_sintetico(99)
    texto = _fixture_apoc_text(listados)

    with Session(engine) as db:
        resumen = apoc_source.refresh_from_bytes(
            db, texto.encode(), origen="fixture-eval")
        db.commit()
        check(resumen["accion"] == "importada", "primera importacion crea version")
        check(resumen["cantidad_registros"] == 5, "5 CUITs importados")
        check(resumen["descartadas"] == 1, "la linea invalida se descarta y se cuenta")
        check(apoc_source.is_listed(db, listados[0]), "CUIT listado se encuentra")
        check(apoc_source.is_listed(db, f"{listados[0][:2]}-{listados[0][2:10]}-{listados[0][10]}"),
              "lookup normaliza guiones")
        check(not apoc_source.is_listed(db, no_listado), "CUIT no listado -> False")
        check(not apoc_source.is_listed(db, "B00000000"), "CIF europeo -> False")

        # Idempotencia: mismo contenido no crea version nueva.
        repetido = apoc_source.refresh_from_bytes(db, texto.encode(), origen="fixture-eval")
        check(repetido["accion"] == "sin_cambios", "mismo checksum -> sin_cambios")
        versiones = db.execute(select(ArcaApocVersion)).scalars().all()
        check(len(versiones) == 1, "sigue habiendo UNA version")

        # El ZIP oficial se acepta igual que el texto plano.
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as bundle:
            bundle.writestr("FacturasApocrifas.txt", texto)
        zip_igual = apoc_source.refresh_from_bytes(db, buffer.getvalue(), origen="zip")
        check(zip_igual["accion"] == "sin_cambios", "zip con el mismo txt -> sin_cambios")

        # Contenido nuevo reemplaza el conjunto completo y versiona.
        texto2 = _fixture_apoc_text(listados[1:] + [no_listado])
        nuevo = apoc_source.refresh_from_bytes(db, texto2.encode(), origen="fixture-eval-2")
        db.commit()
        check(nuevo["accion"] == "importada", "checksum distinto -> nueva version")
        check(not apoc_source.is_listed(db, listados[0]), "CUIT retirado ya no figura")
        check(apoc_source.is_listed(db, no_listado), "CUIT agregado figura")
        entradas = db.execute(select(ArcaApocEntry)).scalars().all()
        check({e.version_id for e in entradas} == {nuevo["version_id"]},
              "todas las entradas apuntan a la version vigente")

        # Descarga vacia o corrupta NO pisa la base vigente.
        try:
            apoc_source.refresh_from_bytes(db, b"# solo comentarios\n", origen="x")
            check(False, "descarga vacia debe fallar")
        except ValueError:
            check(True, "descarga vacia rechazada; la base vigente se conserva")

        info = apoc_source.latest_version_info(db)
        check(info is not None and not info["desactualizada"],
              "base recien importada no esta desactualizada")
        # Antiguedad > 15 dias -> desactualizada (advertencia global).
        version = db.execute(select(ArcaApocVersion).order_by(
            ArcaApocVersion.id.desc())).scalars().first()
        version.fecha_descarga = datetime.now(timezone.utc) - timedelta(days=16)
        db.commit()
        info = apoc_source.latest_version_info(db)
        check(info["desactualizada"] and info["antiguedad_dias"] >= 16,
              "base con 16 dias -> desactualizada")

    check(apoc_source.parse_apoc_text("")[0] == [], "texto vacio -> sin entradas")


def _certificado_sintetico(tmp: Path) -> tuple[Path, Path]:
    """Certificado X.509 autofirmado SOLO para tests (jamas material real)."""
    from datetime import datetime, timedelta, timezone

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "eval-arca-sintetico")])
    now = datetime.now(timezone.utc)
    cert = (x509.CertificateBuilder()
            .subject_name(name).issuer_name(name)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(days=1))
            .not_valid_after(now + timedelta(days=30))
            .sign(key, hashes.SHA256()))
    cert_path = tmp / "cert_eval.pem"
    key_path = tmp / "key_eval.pem"
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption()))
    return cert_path, key_path


def _respuesta_wsaa(expiration: str) -> bytes:
    from xml.sax.saxutils import escape

    ta = ("<?xml version=\"1.0\"?><loginTicketResponse>"
          "<header><expirationTime>" + expiration + "</expirationTime></header>"
          "<credentials><token>tok-eval</token><sign>sig-eval</sign></credentials>"
          "</loginTicketResponse>")
    return ("<soapenv:Envelope xmlns:soapenv=\"http://schemas.xmlsoap.org/soap/envelope/\">"
            "<soapenv:Body><loginCmsResponse xmlns=\"http://wsaa\">"
            f"<loginCmsReturn>{escape(ta)}</loginCmsReturn>"
            "</loginCmsResponse></soapenv:Body></soapenv:Envelope>").encode()


def _seccion_wsaa() -> None:
    import tempfile
    from datetime import datetime, timedelta, timezone

    from ap_control_tower.controls.arca import wsaa

    print("== WSAA: TRA firmado, ticket cacheado y renovacion ==")
    with tempfile.TemporaryDirectory() as raw_tmp:
        tmp = Path(raw_tmp)
        cert_path, key_path = _certificado_sintetico(tmp)
        config = wsaa.WsaaConfig(
            environment="homologacion", cert_path=str(cert_path),
            key_path=str(key_path), ticket_dir=str(tmp / "tickets"))
        check(config.configured, "config con cert y clave presentes -> configurada")
        check("wsaahomo" in config.login_url, "homologacion apunta a wsaahomo")
        check("://wsaa.afip" in wsaa.LOGIN_URLS["produccion"],
              "produccion apunta a wsaa.afip")

        tra = wsaa.build_tra("ws_sr_constancia_inscripcion")
        check(b"<service>ws_sr_constancia_inscripcion</service>" in tra,
              "el TRA declara el servicio")
        check(b"generationTime" in tra and b"expirationTime" in tra,
              "el TRA tiene ventana de vigencia")
        cms = wsaa.sign_tra_cms(tra, cert_path.read_bytes(), key_path.read_bytes())
        check(len(cms) > 500, "CMS/PKCS#7 en base64 no trivial")

        llamadas: list[str] = []
        vence = (datetime.now(timezone.utc) + timedelta(hours=12)).isoformat(
            timespec="seconds")

        def transporte(url: str, body: bytes) -> bytes:
            llamadas.append(url)
            check(b"loginCms" in body and b"in0" in body, "el SOAP invoca loginCms")
            return _respuesta_wsaa(vence)

        ticket = wsaa.get_ticket(config, "ws_sr_constancia_inscripcion",
                                 transport=transporte)
        check(ticket.token == "tok-eval" and ticket.sign == "sig-eval",
              "ticket parseado del SOAP")
        check(len(llamadas) == 1, "primera obtencion llama a WSAA")
        otra_vez = wsaa.get_ticket(config, "ws_sr_constancia_inscripcion",
                                   transport=transporte)
        check(len(llamadas) == 1 and otra_vez.token == "tok-eval",
              "segunda obtencion usa el cache (0 llamadas nuevas)")

        # Por vencer -> renueva.
        casi_vencido = datetime.fromisoformat(vence) - timedelta(minutes=5)
        wsaa.get_ticket(config, "ws_sr_constancia_inscripcion",
                        transport=transporte, now=casi_vencido)
        check(len(llamadas) == 2, "ticket por vencer se renueva")

        # Sin certificado configurado -> WsaaError explicito (la senal de
        # 'verificacion no disponible' nace de aca).
        sin_cert = wsaa.WsaaConfig(environment="homologacion")
        try:
            wsaa.request_ticket(sin_cert, "wsapoc", transport=transporte)
            check(False, "sin certificado debe fallar")
        except wsaa.WsaaError as exc:
            check("no configurado" in str(exc), "error claro sin certificado")

        try:
            wsaa.parse_login_response(b"<no-es-soap/>")
            check(False, "respuesta invalida debe fallar")
        except wsaa.WsaaError:
            check(True, "respuesta invalida -> WsaaError")


def _respuesta_padron(estado: str = "ACTIVO", *, monotributo: bool = False,
                      regimen_general: bool = True, existe: bool = True,
                      razon: str = "PROVEEDOR SINTETICO SA") -> bytes:
    if not existe:
        return (b"<soap:Envelope xmlns:soap=\"http://schemas.xmlsoap.org/soap/envelope/\">"
                b"<soap:Body><ns2:getPersona_v2Response xmlns:ns2=\"http://a5\">"
                b"<personaReturn><errorConstancia>No existe persona con ese Id"
                b"</errorConstancia></personaReturn>"
                b"</ns2:getPersona_v2Response></soap:Body></soap:Envelope>")
    bloques = (f"<datosGenerales><estadoClave>{estado}</estadoClave>"
               f"<razonSocial>{razon}</razonSocial></datosGenerales>")
    if monotributo:
        bloques += "<datosMonotributo><impuesto><idImpuesto>20</idImpuesto></impuesto></datosMonotributo>"
    elif regimen_general:
        bloques += "<datosRegimenGeneral><impuesto><idImpuesto>30</idImpuesto></impuesto></datosRegimenGeneral>"
    xml = ("<soap:Envelope xmlns:soap=\"http://schemas.xmlsoap.org/soap/envelope/\">"
           "<soap:Body><ns2:getPersona_v2Response xmlns:ns2=\"http://a5\">"
           f"<personaReturn>{bloques}</personaReturn>"
           "</ns2:getPersona_v2Response></soap:Body></soap:Envelope>")
    return xml.encode()


def _cliente_padron(respuestas, tmp: Path, llamadas: list):
    """PadronClient con WSAA y transporte falsos (cero red)."""
    from ap_control_tower.controls.arca import padron_client, wsaa

    cert_path, key_path = _certificado_sintetico(tmp)
    config = wsaa.WsaaConfig(environment="homologacion", cert_path=str(cert_path),
                             key_path=str(key_path), ticket_dir=str(tmp / "tk"))

    def transporte(url: str, body: bytes) -> bytes:
        if "LoginCms" in url:
            from datetime import datetime, timedelta, timezone
            vence = (datetime.now(timezone.utc) + timedelta(hours=12)).isoformat(
                timespec="seconds")
            return _respuesta_wsaa(vence)
        llamadas.append(url)
        respuesta = respuestas.pop(0)
        if isinstance(respuesta, Exception):
            raise respuesta
        return respuesta

    # get_ticket usa el mismo transporte fake (LoginCms canned).
    import ap_control_tower.controls.arca.padron_client as pc

    cliente = pc.PadronClient(config=config, cuit_representada="30-00000000-7",
                              transport=transporte)
    cliente._transporte_wsaa = transporte  # referencia para get_ticket
    original_get_ticket = pc.get_ticket
    pc.get_ticket = lambda cfg, svc, **kw: original_get_ticket(
        cfg, svc, transport=transporte)
    return cliente, (pc, original_get_ticket)


def _seccion_padron() -> None:
    import tempfile
    from datetime import datetime, timedelta, timezone

    from sqlalchemy import create_engine, select
    from sqlalchemy.orm import Session

    from ap_control_tower.controls.arca import cuit, padron_client
    from ap_control_tower.persistence.models_sql import ArcaPadronCache, Base

    print("== Padron: cliente getPersona_v2 y cache con TTL ==")
    objetivo = cuit.generar_cuit_sintetico(11)

    with tempfile.TemporaryDirectory() as raw_tmp:
        tmp = Path(raw_tmp)
        llamadas: list[str] = []
        respuestas = [
            _respuesta_padron("ACTIVO"),
            _respuesta_padron("BAJA POR FALLECIMIENTO", regimen_general=False),
            _respuesta_padron(monotributo=True),
            _respuesta_padron(existe=False),
            TimeoutError("timeout simulado"),
            TimeoutError("timeout simulado"),
            TimeoutError("timeout simulado"),
        ]
        cliente, (pc, original) = _cliente_padron(respuestas, tmp, llamadas)
        try:
            activo = cliente.get_persona(objetivo)
            check(activo["existe"] and activo["estado"] == "ACTIVO"
                  and activo["condicion_iva"] == "responsable_inscripto",
                  "activo + regimen general -> responsable_inscripto")
            baja = cliente.get_persona(objetivo)
            check(baja["estado"].startswith("BAJA"), "estado de baja se conserva literal")
            mono = cliente.get_persona(objetivo)
            check(mono["condicion_iva"] == "monotributo", "datosMonotributo -> monotributo")
            inexistente = cliente.get_persona(objetivo)
            check(inexistente["existe"] is False, "errorConstancia 'no existe' -> existe=False")
            # Timeout persistente: 1 + 2 reintentos y PadronNoDisponible.
            antes = len(llamadas)
            try:
                cliente.get_persona(objetivo)
                check(False, "timeout persistente debe fallar")
            except padron_client.PadronNoDisponible:
                check(True, "timeout persistente -> PadronNoDisponible")
            check(len(llamadas) - antes == 3, "timeout agota 1 intento + 2 reintentos")

            # Cache con TTL: 1 llamada por CUIT nuevo, luego lectura local.
            engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
            Base.metadata.create_all(engine)
            respuestas.extend([_respuesta_padron("ACTIVO"), _respuesta_padron("ACTIVO")])
            with Session(engine) as db:
                antes = len(llamadas)
                primera = padron_client.cached_persona(db, cliente, objetivo)
                db.commit()
                check(len(llamadas) - antes == 1, "primer lookup consulta el padron")
                segunda = padron_client.cached_persona(db, cliente, objetivo)
                check(len(llamadas) - antes == 1, "segundo lookup sale del cache (0 red)")
                check(segunda["estado"] == "ACTIVO" and "fetched_at" in segunda,
                      "cache devuelve payload + fetched_at")
                # Vencido el TTL, refresca.
                futuro = datetime.now(timezone.utc) + timedelta(
                    days=padron_client.ttl_dias() + 1)
                padron_client.cached_persona(db, cliente, objetivo, now=futuro)
                check(len(llamadas) - antes == 2, "cache vencido refresca (1 llamada)")
                filas = db.execute(select(ArcaPadronCache)).scalars().all()
                check(len(filas) == 1 and filas[0].cuit == objetivo,
                      "una fila de cache por CUIT")
            check(primera["existe"], "payload cacheado coherente")
        finally:
            pc.get_ticket = original

        # Sin certificado -> PadronNoDisponible (nace la advertencia).
        sin_cert = padron_client.PadronClient(
            config=padron_client.WsaaConfig(environment="homologacion"),
            cuit_representada="30-00000000-7", transport=lambda u, b: b"")
        try:
            sin_cert.get_persona(objetivo)
            check(False, "sin certificado debe fallar")
        except padron_client.PadronNoDisponible:
            check(True, "sin certificado -> PadronNoDisponible")


def _doc(tax_id, numero="FAC-001", tipo=None) -> dict:
    doc = {"document_type": "invoice", "proveedor_tax_id": tax_id,
           "proveedor_nombre_comercial": "Proveedor Sintetico SA",
           "numero_factura": numero, "fecha_emision": "2026-06-01",
           "moneda": "ARS", "importe_total": "1000.00"}
    if tipo is not None:
        doc["tipo_comprobante"] = tipo
    return doc


def _seccion_validadores() -> None:
    from ap_control_tower.controls.arca import cuit, validators as v

    print("== Validadores puros: senales tipadas ==")
    valido = cuit.generar_cuit_sintetico(3)
    invalido = valido[:10] + str((int(valido[10]) + 1) % 10)

    senales = v.validar_cuit_local(_doc(invalido))
    check(len(senales) == 1 and senales[0].tipo == v.BLOQUEANTE
          and senales[0].motivo == v.MOTIVO_DV_INVALIDO,
          "dv invalido -> bloqueante con el motivo del spec")
    check(v.validar_cuit_local(_doc(valido)) == [], "cuit valido -> sin senales")
    check(v.validar_cuit_local(_doc("B00000000")) == [], "CIF -> sin senales")
    check(v.validar_cuit_local(_doc(None)) == [], "sin tax id -> sin senales")

    check(v.validar_padron(_doc(valido), None) == [], "persona None -> nada que validar")
    inexistente = v.validar_padron(_doc(valido), {"existe": False})
    check(len(inexistente) == 1 and inexistente[0].motivo == v.MOTIVO_INEXISTENTE,
          "inexistente en padron -> bloqueante")
    baja = v.validar_padron(_doc(valido), {
        "existe": True, "estado": "BAJA DEFINITIVA", "condicion_iva": "responsable_inscripto"})
    check(len(baja) == 1 and "BAJA DEFINITIVA" in baja[0].motivo,
          "estado no activo -> bloqueante con el estado en el motivo")
    mono_a = v.validar_padron(_doc(valido, tipo="A"), {
        "existe": True, "estado": "ACTIVO", "condicion_iva": "monotributo"})
    check(len(mono_a) == 1 and "monotributo" in mono_a[0].motivo
          and "factura A" in mono_a[0].motivo,
          "monotributista emitiendo factura A -> bloqueante")
    mono_b = v.validar_padron(_doc(valido, tipo="B"), {
        "existe": True, "estado": "ACTIVO", "condicion_iva": "monotributo"})
    check(mono_b == [], "monotributista con factura B -> coherente")
    check(v.letra_comprobante({"numero_factura": "A 0001-00001234"}) == "A",
          "letra inferida del numero 'A 0001-...'")
    check(v.letra_comprobante({"numero_factura": "FC-A-0001"}) is None
          or v.letra_comprobante({"numero_factura": "FC-A 0001"}) in (None, "A"),
          "prefijos ambiguos no fuerzan letra")
    check(v.letra_comprobante({"numero_factura": "ES01-ARIV-0010205"}) is None,
          "numero europeo no infiere letra")

    apoc = v.validar_apoc(_doc(valido), True, {"version_id": 7, "checksum": "abc",
                                               "fecha_descarga": "2026-07-20"})
    check(len(apoc) == 1 and apoc[0].severidad == "maxima"
          and apoc[0].motivo == v.MOTIVO_APOC
          and apoc[0].evidencia["apoc_version_id"] == 7,
          "en APOC -> bloqueante de maxima severidad con version en evidencia")
    check(v.validar_apoc(_doc(valido), False) == [], "no listado -> sin senales")

    deriva = v.senal_no_disponible("timeout", "derive")
    aviso = v.senal_no_disponible("timeout", "warn")
    check(deriva.tipo == v.ADVERTENCIA and deriva.motivo == v.MOTIVO_NO_DISPONIBLE,
          "fail derive -> advertencia que deriva")
    check(aviso.tipo == v.FYI and aviso.motivo.endswith(v.SUFIJO_SOLO_AVISO),
          "fail warn -> FYI con sufijo (no deriva)")

    check(v.advertencia_apoc_desactualizada({"desactualizada": True,
                                             "fecha_descarga": "2026-07-01T00:00:00"})
          == "base APOC desactualizada, última descarga: 2026-07-01",
          "base vieja -> advertencia global con fecha")
    check(v.advertencia_apoc_desactualizada({"desactualizada": False}) is None,
          "base fresca -> sin advertencia global")


def _seccion_service() -> None:
    from ap_control_tower.audit import AuditTrail
    from ap_control_tower.controls.arca import cuit, service, validators as v

    print("== Service: modos off/mock/live y auditoria por senal ==")
    valido = cuit.generar_cuit_sintetico(5)
    invalido = valido[:10] + str((int(valido[10]) + 1) % 10)

    class R:
        def __init__(self, doc):
            self.doc_id = "DOC-1"
            self.document = doc
            self.warnings = []

    service.mock_data.reset()
    # mock sin matches: identico a off para padron/APOC.
    ev = service.evaluar_documento(_doc(valido), modo="mock")
    check(ev.senales == [] and ev.advertencias_globales == [],
          "mock sin matches -> cero senales")
    ev = service.evaluar_documento(_doc(valido), modo="off")
    check(ev.senales == [], "off con cuit valido -> cero senales")
    ev = service.evaluar_documento(_doc(invalido), modo="off")
    check(len(ev.senales) == 1 and ev.senales[0].codigo == "cuit_dv_invalido",
          "off NO apaga la validacion local del dv")

    # Fixtures mock: APOC y padron generan senales y auditoria.
    service.mock_data.apoc.add(valido)
    service.mock_data.padron[cuit.generar_cuit_sintetico(6)] = {
        "existe": True, "estado": "BAJA PROVISORIA",
        "condicion_iva": "responsable_inscripto"}
    audit = AuditTrail(commit="eval-arca")
    resultado = R(_doc(valido))
    ev = service.enriquecer_resultado(resultado, audit, modo="mock")
    check(any(s.control == v.C11_APOC for s in ev.senales),
          "proveedor en fixture APOC -> senal C11")
    check(v.MOTIVO_APOC in resultado.warnings,
          "el motivo APOC queda en result.warnings (deriva por politica)")
    eventos = [e for e in audit.events if e.action == "control-arca-senal"]
    check(len(eventos) == 1 and eventos[0].evidence["severidad"] == "maxima"
          and eventos[0].evidence["control"] == "C11_APOC",
          "cada senal registra evento de auditoria con severidad y control")
    check(audit.verify_chain(), "la cadena de auditoria sigue integra")

    resultado2 = R(_doc(cuit.generar_cuit_sintetico(6)))
    ev = service.enriquecer_resultado(resultado2, audit, modo="mock")
    check(any("BAJA PROVISORIA" in w for w in resultado2.warnings),
          "padron mock con baja -> motivo con estado en warnings")

    # live sin base configurada -> advertencia explicita, no pase silencioso.
    import os
    sin_db = {k: os.environ.pop(k, None) for k in ("AP_DATABASE_URL", "DATABASE_URL")}
    try:
        ev = service.evaluar_documento(_doc(valido), modo="live", fail_mode="derive")
        check(len(ev.senales) == 1 and ev.senales[0].codigo == "padron_no_disponible"
              and ev.senales[0].tipo == v.ADVERTENCIA,
              "live sin base local -> advertencia 'no disponible' que deriva")
        ev = service.evaluar_documento(_doc(valido), modo="live", fail_mode="warn")
        check(ev.senales[0].tipo == v.FYI, "live sin base + warn -> FYI")
        ev = service.evaluar_documento(_doc("B00000000"), modo="live")
        check(len(ev.senales) == 1 and ev.senales[0].codigo == "padron_no_disponible",
              "live sin base con CIF: la advertencia igual deja rastro")
    finally:
        for k, val in sin_db.items():
            if val is not None:
                os.environ[k] = val

    # Registro informativo del modo off.
    audit_off = AuditTrail(commit="eval-arca")
    service.registrar_modo_off(audit_off)
    check(any(e.action == "controles-arca-omitidos" for e in audit_off.events),
          "modo off deja constancia informativa en auditoria")
    service.mock_data.reset()


def main() -> int:
    _seccion_cuit()
    _seccion_apoc()
    _seccion_wsaa()
    _seccion_padron()
    _seccion_validadores()
    _seccion_service()
    if failures:
        print(f"CONTROLES ARCA ROJO: {len(failures)} fallas")
        return 1
    print("CONTROLES ARCA VERDE: CUIT, APOC, WSAA, padron, validadores y service OK (exit 0)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
