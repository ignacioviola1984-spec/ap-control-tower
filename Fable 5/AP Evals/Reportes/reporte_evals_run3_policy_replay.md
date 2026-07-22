# Reporte de evals · run3 policy replay offline

Fecha UTC: 2026-07-16T12:53:20+00:00  
Golden dataset: v1.0 corregido (106 documentos)  
Extraccion reutilizada: run2  
Llamadas a Document AI: **0**

## 1. Que valida esta corrida

Esta corrida reconstruye los 106 resultados estructurados guardados en run2 y
ejecuta la version actual de las reglas puras de revision y duplicados. No abre
PDFs, no vuelve a extraer y no consume credito de Google.

Es una regresion integral de la **politica evaluable con los datos persistidos**.
No valida nuevamente el extractor ni los controles que requieren maestros o
campos que run2 no exporto.

## 2. Resultados comparables (alcance formal: 96 documentos)

| Metrica | run2 reconstruido | run3 policy replay | Delta |
|---|---:|---:|---:|
| Exactitud de ruteo | 94.8% | 96.9% | +2.1 pts |
| Precision de derivacion | 42.9% | 57.1% | +14.2 pts |
| Recall de derivacion | 75.0% | 100.0% | +25.0 pts |
| Falsos negativos | 1 | 0 | -1 |
| Derivados sobre los 106 golden | 10/106 (9.4%) | 11/106 (10.4%) | +1.0 pts |

El recall vuelve a 100%: GD-119 ahora se deriva como posible nota de credito.
La exactitud de ruteo sube a 96.9%. La tasa de revision aumenta en un caso neto
porque run3 libera GD-018 y agrega GD-107 y GD-119.

## 3. Cambios de ruta

| Documento | run2 | run3 | Esperado | Lectura |
|---|---|---|---|---|
| GD-018 | revision_humana | en_lote | en_lote | libera un falso positivo de run2 |
| GD-107 | en_lote | revision_humana | bloqueada | el fix detecta el casi-duplicado C2 |
| GD-119 | en_lote | revision_humana | revision_humana | el fix recupera el falso negativo de run2 |

## 4. Cobertura de controles offline

- **C2 duplicados:** 3/3 casos que debian bloquearse reciben senal de duplicado
  (GD-106, GD-107 y GD-108). El detector tambien retiene la original GD-105;
  sigue siendo una decision funcional pendiente si el grupo completo debe
  frenarse hasta revision.
- **Nota de credito:** GD-119 pasa de `en_lote` a `revision_humana`.
- **C9 vendor master:** la advertencia persistida de GD-115 sigue derivando,
  pero este replay no vuelve a consultar el maestro.

No son evaluables offline con el export de run2:

- C6 datos bancarios: 2 casos; faltan IBAN extraido completo y maestro bancario.
- C3 autorizacion de OC: 2 casos; falta maestro/estado/saldo de OC.
- C5 match: 1 caso; falta el estado de la OC y su tolerancia aplicada.
- C8 anticipo y la ruta de conciliacion: el documento se reconoce, pero el CSV
  no conserva todo el estado necesario para validar la ruta especializada.

Por esta limitacion **no se declara todavia cero escapes de riesgo de pago para
el conjunto C2-C9**. Esa afirmacion queda reservada al cloud smoke autorizado.

## 5. Trazabilidad

- Golden SHA-256: `0291f0a34dbe3ae72380e5c5989a6a1062b09fc8dd8cabe927845c847e4cd944`
- Extraccion run2 SHA-256: `483734977dcb51fee5fb3ded7facec4197da077bd10d31e0553b5628daaece28`
- workflow.py SHA-256: `1521ced381328ca76b57961d848d407e6be28c8942ae5d58da071957b30c534c`
- Commit observado: `8fc27fd6dd804745012adeaba0e8f7ea9166e0b7`
- Working tree dirty: `true`

El detalle documento por documento y el manifest JSON permiten repetir y
auditar el calculo sin procesar facturas nuevamente.

## 6. Decision sobre el cloud smoke

La muestra dirigida contiene 15 documentos:
los fixes, todos los controles no evaluables offline y tres controles negativos.
El smoke queda **diferido hasta que las integraciones con Zoho/Sage y los
maestros requeridos esten conectados al circuito evaluable**. Ejecutarlo antes
solo reconfirmaria los fixes de politica y no agregaria evidencia comercial
material sobre C3, C5 o C6.

No se realizo ninguna llamada cloud durante esta corrida. Los candidatos quedan
versionados para reutilizarlos cuando se cumplan las condiciones de entrada.
