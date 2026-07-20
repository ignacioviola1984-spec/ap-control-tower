# Runbook · Controles ARCA (C10 padrón + C11 APOC)

Operación de los controles argentinos del AP Control Tower: validación del
CUIT del proveedor contra el padrón de ARCA (constancia de inscripción) y
verificación contra la base de facturas apócrifas (APOC). Este documento lo
ejecuta un operador humano sin contexto previo del desarrollo.

**Regla de producto:** los dos controles son validaciones deterministas.
Derivan a revisión humana con motivo explícito; jamás auto-aprueban ni
deciden por score. La ausencia de verificación NUNCA pasa en silencio: deja
advertencia visible en el documento y evento en el audit trail.

---

## 1. Constancia del Paso 0 · fuentes verificadas (2026-07-20)

| Fuente | Qué se verificó | URL |
|---|---|---|
| Catálogo WS ARCA | `ws_sr_padron_a5` figura **deprecado**; el vigente es `ws_sr_constancia_inscripcion` (manual v4.1, 27/02/2026). Existe `wsapoc` (manual v1.0.9, 21/01/2025). | https://www.afip.gob.ar/ws/documentacion/catalogo.asp |
| Manual constancia v4.1 | Endpoints: homologación `https://awshomo.arca.gob.ar/sr-padron/webservices/personaServiceA5?WSDL`, producción `https://aws.arca.gob.ar/sr-padron/webservices/personaServiceA5?WSDL` (v4.1 migró los links de afip→arca). Métodos: `dummy`, `getPersona_v2`, `getPersonaList_v2`. Devuelve `estadoClave`, `datosRegimenGeneral` (IVA), `datosMonotributo`. | https://www.afip.gob.ar/ws/WSCI/manual_ws_sr_ws_constancia_inscripcion.pdf |
| WSAA | Endpoints LoginCms: homologación `https://wsaahomo.afip.gov.ar/ws/services/LoginCms`, producción `https://wsaa.afip.gov.ar/ws/services/LoginCms`. Certificado X.509 emitido por ARCA; ticket ~12 h. | https://www.afip.gob.ar/ws/documentacion/wsaa.asp |
| APOC · webservice | `wsapoc` requiere WSAA (tag `service=wsapoc`). Homologación `https://eapoc-ws-qaext.afip.gob.ar/Service.asmx?WSDL`, producción `https://eapoc-ws.afip.gob.ar/service.asmx`. Consulta por CUIT, nómina completa y movimientos por fechas; base en tiempo real. | https://servicioscf.afip.gob.ar/Facturacion/facturasApocrifas/WS-factura-apoc.pdf y https://www.afip.gob.ar/ws/wsapoc/ManualUsuario-1.0.9.pdf |
| APOC · descarga pública | **Sin autenticación.** `DownloadFile.aspx` entrega `FacturasApocrifas.zip` con `FacturasApocrifas.txt` (verificado en vivo: 45.167 CUITs; cabecera `# Generado - 20/7/2026`, el archivo se regenera a diario). Estructura: `CUIT, Fecha Condición Apócrifo, Fecha Publicación, Descripción`. | https://servicioscf.afip.gob.ar/Facturacion/facturasApocrifas/DownloadFile.aspx |

**Decisión de diseño (Paso 0):** para APOC se usa la **descarga pública**
persistida localmente y refrescada por job (preferencia del spec: consulta
local sin llamada de red por documento). No requiere certificado ni WSAA.
El WS `wsapoc` (tiempo real) queda documentado como alternativa para una
fase posterior. El padrón sí exige WSAA + certificado: no existe vía pública.

**Riesgo residual documentado:** la validación local de CUIT solo evalúa
"candidatos" (11 dígitos tras quitar separadores). Un IVA extranjero de 11
dígitos (p. ej. partita IVA italiana) con prefijo coincidente (20-27/30/33/34)
podría evaluarse como CUIT. En el mercado objetivo (proveedores argentinos)
el riesgo es marginal; si aparece un falso positivo, el humano lo resuelve en
Revisión con el motivo a la vista.

---

## 2. Modos de operación

| Modo | Qué hace | Requisitos |
|---|---|---|
| `AP_ARCA_MODE=off` | Padrón y APOC no verifican (constancia informativa en el audit trail). La validación local del dígito verificador corre igual. | Ninguno |
| `AP_ARCA_MODE=mock` (default) | Fixtures locales en memoria (dev/tests). Sin fixtures cargadas se comporta igual que `off` para el ruteo. | Ninguno |
| `AP_ARCA_MODE=live` | Padrón real (WSAA + cache local con TTL) y APOC local (tabla refrescada por job). | Base local + certificado WSAA |

Ante padrón caído / timeout / certificado ausente:

- `AP_ARCA_FAIL_MODE=derive` (default): advertencia **"verificación contra
  padrón ARCA no disponible: documento no verificado"** y el documento se
  deriva a revisión humana.
- `AP_ARCA_FAIL_MODE=warn`: la misma advertencia queda visible en el
  documento y en el audit trail, pero no deriva (sufijo "modo aviso").

## 3. Variables de entorno (plantilla con placeholders)

```bash
# --- modo de operación
AP_ARCA_MODE=live                 # off | mock | live
AP_ARCA_FAIL_MODE=derive          # warn | derive (default derive)

# --- base local (la misma de la capa de persistencia)
AP_DATABASE_URL=postgresql+psycopg://USUARIO:PASSWORD@HOST:5432/BASE

# --- WSAA (solo live). Rutas a archivos montados desde Secret Manager:
AP_ARCA_ENV=produccion            # homologacion | produccion
AP_ARCA_CERT_PATH=/var/secrets/arca/certificado.pem
AP_ARCA_KEY_PATH=/var/secrets/arca/clave_privada.pem
AP_ARCA_CUIT_REPRESENTADA=XX-XXXXXXXX-X   # CUIT titular del certificado
AP_ARCA_TICKET_DIR=/var/cache/arca        # cache de tickets (default ~/.ap_control_tower/wsaa)

# --- ajustes opcionales
AP_ARCA_PADRON_TTL_DIAS=7         # TTL del cache de constancia (default 7)
```

Cero credenciales en el repo: certificado y clave viven en Secret Manager y
se montan como archivos. Dependencias del modo live:
`pip install -r requirements-persistence.txt -r requirements-arca.txt`.

## 4. Certificado WSAA: obtención paso a paso

### 4.1 Homologación (para probar sin riesgo)

1. Ingresar con **Clave Fiscal** (nivel 3+) a https://auth.afip.gob.ar y
   habilitar el servicio **"Autogestión de certificados para Servicios Web en
   los ambientes de homologación" (WSASS)** desde el *Administrador de
   Relaciones de Clave Fiscal*.
2. En WSASS: crear un DN, pegar un CSR (ver 4.3) y obtener el certificado de
   homologación.
3. En WSASS, asignar al certificado el acceso a los servicios
   `ws_sr_constancia_inscripcion` (y `wsapoc` si se usará el WS de APOC).
4. Configurar `AP_ARCA_ENV=homologacion` y probar (sección 6).

### 4.2 Producción

1. Con Clave Fiscal, entrar a **"Administración de Certificados Digitales"**.
2. Crear un alias ("computador fiscal") y cargar el CSR (ver 4.3): se obtiene
   el certificado X.509 emitido por la AC de ARCA.
3. En **"Administrador de Relaciones de Clave Fiscal"**, delegar el servicio
   `ws_sr_constancia_inscripcion` (y `wsapoc` si aplica) al computador fiscal
   creado (relación servicio ↔ certificado).
4. Guardar certificado y clave privada en Secret Manager; NUNCA en el repo ni
   en la imagen. `AP_ARCA_CUIT_REPRESENTADA` = CUIT del titular del
   certificado.

### 4.3 Generar clave y CSR (una sola vez, en máquina segura)

```bash
openssl genrsa -out clave_privada.pem 2048
openssl req -new -key clave_privada.pem -subj \
  "/C=AR/O=NOMBRE-EMPRESA/CN=ap-control-tower/serialNumber=CUIT XXXXXXXXXXX" \
  -out pedido.csr
```

## 5. Refresh de la base APOC (job)

```bash
# descarga oficial pública + import versionado (idempotente por checksum)
AP_DATABASE_URL=... python -m ap_control_tower.controls.arca.refresh_apoc

# alternativa: importar un archivo ya descargado (zip o txt)
AP_DATABASE_URL=... python -m ap_control_tower.controls.arca.refresh_apoc --file FacturasApocrifas.zip
```

- **Frecuencia recomendada: diaria** (ARCA regenera el archivo cada día).
  Programarlo en cron / Cloud Scheduler + Cloud Run Job; el scheduler NO se
  gestiona desde este repo, solo el comando.
- Exit 0 = importada o sin cambios; exit 1 = error (la base vigente queda
  intacta: el import es transaccional).
- Cada import queda versionado en `arca_apoc_versions` (fecha, checksum
  SHA-256, cantidad, origen); los eventos de auditoría de C11 referencian esa
  versión.
- Con la base a más de **15 días**, cada corrida muestra la advertencia
  global "base APOC desactualizada, última descarga: FECHA".

## 6. Verificación end-to-end (homologación)

1. `AP_ARCA_MODE=live`, `AP_ARCA_ENV=homologacion`, certificado de WSASS.
2. Correr el refresh APOC (sección 5) contra la base local.
3. Procesar una factura de prueba con CUIT conocido: en la vista de
   Revisión humana deben aparecer los motivos ARCA cuando correspondan, y en
   el Registro de auditoría los eventos `control-arca-senal`.
4. Suite hermética: `python evals/test_controles_arca.py` (exit 0, sin red).

## 7. Troubleshooting

| Síntoma | Causa probable | Acción |
|---|---|---|
| Advertencia "verificación contra padrón ARCA no disponible" en todos los documentos | Certificado no configurado, WSAA caído, o falta `AP_ARCA_CUIT_REPRESENTADA` | Revisar variables de la sección 3; probar `wsaa.get_ticket` en homologación; el pipeline NUNCA se bloquea por esto |
| WSAA rechaza el TRA ("ticket vencido", error de vigencia) | Reloj desfasado o ticket cacheado corrupto | Sincronizar NTP; borrar el archivo `ta_*.json` de `AP_ARCA_TICKET_DIR` (se regenera solo) |
| WSAA rechaza el certificado | Certificado vencido o revocado | Renovar en "Administración de Certificados Digitales" y re-delegar los servicios (sección 4.2) |
| "padron devolvio error: ..." persistente con un CUIT puntual | Cambio de contrato en getPersona_v2 | Verificar versión vigente del manual en el catálogo (sección 1) |
| Advertencia "base APOC desactualizada" | El job de refresh no corre | Revisar el scheduler y correr a mano la sección 5 |
| `refresh_apoc` exit 1 "no contiene CUITs" | Descarga corrupta o cambio de formato del TXT | Descargar a mano y comparar con la estructura de la sección 1; la base local vigente no se pisó |
| Docs con CIF/NIF europeos generan señales ARCA | No deberían: solo valores de 11 dígitos son candidatos | Si ocurre, revisar `proveedor_tax_id` extraído; reportar como bug |

## 8. Casos golden nuevos (GD-APOC / GD-PADRON)

`data/golden_casos_arca.csv` (formato de la plantilla del golden dataset)
define: GD-APOC-01 (proveedor apócrifo), GD-PADRON-01 (CUIT de baja),
GD-PADRON-02 (dígito verificador inválido), GD-PADRON-03 (monotributista
emite factura A), GD-PADRON-04 (padrón caído → advertencia, sin pase
silencioso). Son sintéticos (CUITs del generador de `cuit.py`), sin PDF: se
ejercitan en `evals/test_controles_arca.py` con `AP_ARCA_MODE=mock`.

**Importante:** estos casos NO están incorporados a las métricas publicadas
(`evals/quality_summary.json`). Para publicarlos hay que fusionarlos al
golden dataset vigente y correr la evaluación completa; no se inventan
métricas sin corrida que las respalde.

## 9. Fuera de alcance / TODO

- **Validación de CAE por comprobante** (fase posterior): constatación de
  comprobantes emitidos vía el WS **WSCDC** ("Constatación de Comprobantes",
  catálogo https://www.afip.gob.ar/ws/documentacion/catalogo.asp). Exige WSAA
  propio (`wscdc`) y agrega una llamada por comprobante: diseñar con cache y
  cola, no en el camino crítico.
- Consumo del WS `wsapoc` en tiempo real (hoy: descarga pública diaria).
