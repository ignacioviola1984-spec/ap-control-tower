# Pendientes

## UI-001 — Título de “Corrida del mes” recortado en Cloud Run

- **Estado:** pendiente; no corregir todavía.
- **Reportado:** 2026-07-11.
- **Entorno:** demo desplegada en Google Cloud Run, vista de escritorio en Chrome.
- **URL observada:** `https://ap-control-tower-597507822266.us-central1.run.app`
- **Vista afectada:** `Corrida del mes`.
- **Síntoma:** el encabezado principal aparece desplazado hacia arriba y parcialmente recortado/oculto. La parte superior del texto “Corrida del mes” no se ve correctamente, mientras que la tarjeta introductoria y los controles siguientes sí aparecen.
- **Evidencia original:** `C:\Users\ignac\AppData\Local\Temp\codex-clipboard-ff3bbc93-16cd-4638-8e63-dc38417b144b.png`.
- **Alcance futuro:** revisar el espaciado superior y las reglas CSS que afectan el encabezado/contenedor principal en el despliegue, incluyendo el comportamiento responsive. No modificar la lógica de procesamiento.
- **Criterio de aceptación:** el título debe verse completo, sin superposición ni recorte, en Chrome de escritorio y en los anchos de pantalla soportados; el resto del layout debe conservar su posición y estilo.

## DATA-001 — Número no extraído en documento GESMAR

- **Estado:** pendiente; no corregir todavía.
- **Reportado:** 2026-07-11.
- **Documento:** `04. GESMAR P-07-2026 BMC.pdf`.
- **Clasificación obtenida:** `proforma_or_advance_request`.
- **Síntoma:** el resultado muestra `numero = None`, aunque en el documento se observa claramente el campo `NÚMERO: BMC 07/2026`.
- **Valor esperado:** `BMC 07/2026`.
- **Evidencia del resultado:** `C:\Users\ignac\AppData\Local\Temp\codex-clipboard-d537e06f-a801-4143-bb34-6308bdf1bb5f.png`.
- **Evidencia documental:** `C:\Users\ignac\AppData\Local\Temp\codex-clipboard-ea507d27-1e26-46e2-bfe7-4b9f39f64119.png`.
- **Observación funcional:** el documento es un `Presupuesto por anticipo de incentivos`, no una factura convencional. La futura corrección debe conservar esta clasificación y admitir un número propio del documento de anticipo/proforma.
- **Alcance futuro:** revisar el mapeo y la normalización del número documental para tipos `proforma_or_advance_request`, incluyendo posibles campos alternativos devueltos por Document AI y extracción de respaldo desde el texto visible.
- **Criterio de aceptación:** al procesar este PDF, el sistema debe conservar la clasificación correcta y devolver `BMC 07/2026` como número del documento, con evidencia de origen y sin confundirlo con un número de factura u OC.
