# Conector Zoho CRM + WorkDrive

Estado: LISTO en sandbox US. Verificado el 2026-07-16.

## Alcance

- Zoho Analytics: sincronizacion read-only de las seis vistas analytics.v_*.
- Zoho CRM: lectura de metadata y proveedores; capacidad acotada de crear Tasks.
- Zoho WorkDrive: lectura de la carpeta configurada y carga de archivos.
- Fuera de alcance: procesar facturas, publicar propuestas de pago o automatizar
  el flujo AP completo.

## Scopes OAuth

- WorkDrive.files.READ
- WorkDrive.files.CREATE
- ZohoCRM.modules.vendors.READ
- ZohoCRM.modules.tasks.CREATE
- ZohoCRM.settings.modules.READ

## Configuracion

Las credenciales se inyectan mediante Secret Manager; nunca se versionan:

- AP_ZOHO_CLIENT_ID
- AP_ZOHO_CLIENT_SECRET
- AP_ZOHO_REFRESH_TOKEN
- AP_ZOHO_WORKDRIVE_FOLDER_ID
- AP_ZOHO_ACCOUNTS_URL (default https://accounts.zoho.com)
- AP_ZOHO_API_DOMAIN (default https://www.zohoapis.com)

## Criterio de listo

1. OAuth renueva un access token sin mostrarlo.
2. CRM devuelve metadata visible y permite consultar Vendors.
3. WorkDrive lista la carpeta configurada.
4. WorkDrive acepta un archivo de smoke sin datos reales.
5. evals/test_zoho_connector.py pasa en forma hermetica.

## Evidencia 2026-07-16

- Zoho Analytics: origen Google Cloud SQL sincronizado correctamente; seis
  vistas analytics.v_* importadas en AP Control Tower - Sandbox.
- OAuth Self Client: client id, client secret y refresh token almacenados en
  Google Secret Manager. Las versiones iniciales defectuosas quedaron
  deshabilitadas.
- WorkDrive individual sandbox: carpeta AP Control Tower Output creada y su
  resource id almacenado en Secret Manager.
- CRM sandbox: organizacion AP Control Tower Demo inicializada.
- Tests hermeticos:
  - comando: python -m pytest evals/test_zoho_connector.py -q
  - resultado: 6 passed
- Smoke live:
  - crm_modules=true
  - crm_vendors_read=true
  - workdrive_read=true
  - workdrive_upload=true
- Archivo escrito como evidencia: AP_Control_Tower_connector_smoke.txt. No
  contiene datos de facturas ni pagos.

## Secret Manager

- zoho-us-oauth-client-id
- zoho-us-oauth-client-secret
- zoho-us-oauth-refresh-token
- zoho-us-workdrive-folder-id

No se versionan valores sensibles. El client secret usado en sandbox debe
rotarse antes de un piloto porque fue expuesto durante la configuracion.

## Limite de esta entrega

El conector esta autenticado y probado, pero todavia no esta conectado al
workflow de facturas ni desplegado como automatizacion del PoC. Eso queda fuera
del alcance solicitado para este cierre.
