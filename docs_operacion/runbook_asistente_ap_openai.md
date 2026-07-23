# Asistente AP con OpenAI

Fecha: 23/07/2026  
Alcance: piloto Brand UP en Google Cloud Run

## Alcance funcional

El asistente aparece únicamente en:

- Documentos, después del detalle del documento seleccionado.
- Revisión humana, antes del formulario de decisión.

No aparece en Auditoría, Inicio, Ingreso de documentos, Lote de pago ni
Indicadores. La primera versión es estrictamente de solo lectura: explica
motivos de revisión, sintetiza evidencia, sugiere verificaciones e informa el
estado del maestro de proveedores. No modifica campos, no registra decisiones,
no autoriza excepciones y no libera pagos.

## Privacidad por diseño

- Se envían a OpenAI campos estructurados mínimos; nunca el PDF.
- CUIT/ID fiscal, cuentas, IBAN, referencias e identificadores largos se
  enmascaran antes de construir el contexto.
- Cada request a Responses API utiliza `store=false`.
- No se usan Conversations, Assistants, Threads, Files, Vector Stores, web
  search, MCP remoto ni tools alojadas por terceros.
- El historial vive solo en `st.session_state`, separado por corrida,
  documento y página. Se limita a seis mensajes enviados como contexto y doce
  mensajes visibles.
- La auditoría local registra modelo, tools, tokens, latencia y resultado; no
  guarda preguntas, respuestas ni valores documentales.
- Las tools son funciones Python locales y de solo lectura.

La política estándar de OpenAI puede retener logs de abuso hasta 30 días.
`store=false` evita estado de aplicación, pero no reemplaza Zero Data Retention.

## Variables de runtime

Obligatorias para habilitar:

```text
AP_AGENT_ENABLED=1
AP_AGENT_MODEL=gpt-5-mini
OPENAI_API_KEY=<Secret Manager>
```

Opcionales:

```text
AP_AGENT_MAX_HISTORY_MESSAGES=6
AP_AGENT_MAX_OUTPUT_TOKENS=900
```

Si falta el feature flag o la clave, el panel degrada de forma segura y la
revisión determinista continúa disponible.

## Secret Manager y Cloud Run

Crear el secreto en el proyecto correcto de Brand UP y otorgar a la cuenta de
servicio de Cloud Run acceso exclusivo a esa versión. La clave no debe quedar
en archivos, imágenes, argumentos de build, logs o documentación.

La actualización del servicio debe:

1. fijar `AP_AGENT_ENABLED=1`;
2. fijar `AP_AGENT_MODEL=gpt-5-mini`;
3. mapear `OPENAI_API_KEY` desde Secret Manager;
4. conservar el proyecto, región, identidad, processor de Document AI,
   contraseña y variables existentes;
5. desplegar inicialmente sin tráfico o con una revisión controlada;
6. ejecutar smoke con fixtures sintéticos antes de consultar una factura real.

## Panel administrativo opcional

El panel no se registra en la navegación normal. Solo aparece cuando una
instancia administrativa separada define simultáneamente:

```text
AP_AGENT_ADMIN_ENABLED=1
AP_AGENT_ADMIN_PASSWORD=<Secret Manager>
```

Recomendación: habilitarlo en un servicio o revisión administrativa con acceso
IAM restringido. No habilitarlo en la instancia compartida con usuarios de AP.
El panel muestra únicamente metadatos operativos.

## Smoke mínimo

1. Abrir un fixture sintético desde Documentos.
2. Consultar “¿Por qué requiere revisión?”.
3. Verificar respuesta en español y advertencia de decisión humana.
4. Confirmar evento `consulta-asistente-ap` sin prompt ni respuesta.
5. Confirmar `store=false`, `pdf_enviado=false` y `solo_lectura=true`.
6. Repetir desde Revisión humana.
7. Confirmar que Auditoría no contiene un chat.
8. Retirar temporalmente la clave y confirmar degradación segura.

## Rollback

El rollback funcional inmediato es `AP_AGENT_ENABLED=0`; no afecta extracción,
revisión humana, propuesta de pago ni auditoría determinista. El rollback de
despliegue consiste en devolver el tráfico a la revisión anterior de Cloud Run.
