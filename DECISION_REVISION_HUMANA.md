# Decisión funcional — Revisión Humana de documentos reales

**Fecha:** 2026-07-11  
**Estado:** aprobado para planificación e implementación posterior.

## Alcance aprobado

Se aprueba el diseño funcional correspondiente a los puntos 1 a 11 discutidos para integrar los documentos cargados en **PoC documentos reales** con la instancia de **Revisión Humana**.

Quedan expresamente fuera de este momento y se retomarán más adelante:

- Golden Records dentro de la pantalla (punto 12).
- Casos específicos de demostración (punto 13).
- Criterios de aceptación finales (punto 14).

## 1. Estructura de Revisión Humana

La pestaña tendrá cuatro secciones diferenciadas:

1. Documentos por revisar: extracción, clasificación y campos dudosos de PDF reales.
2. Datos internos: centro de costo, aprobador, contrato y soporte de facturas non-PO.
3. Anticipos y proformas: conserva el circuito existente.
4. Maestro de proveedores: altas, información incompleta y cambios bancarios.

## 2. Bandeja de documentos

La bandeja mostrará estado, prioridad, documento, proveedor, tipo, importe, motivo, confianza, antigüedad, responsable y origen. Permitirá filtrar por estado, asignación, confianza, causa, proveedor, importe, fecha, origen y prioridad.

## 3. Pantalla de revisión

El PDF y los campos extraídos deberán verse simultáneamente. La pantalla incluirá encabezado del caso, motivos de revisión, evidencia, campos, controles afectados, historial y acciones humanas.

## 4. Encabezado

Mostrará proveedor, número, tipo documental, importe, moneda, fecha, estado, prioridad, origen, responsable, antigüedad, advertencias e identificador interno. Diferenciará factura, proforma/anticipo, OC/otro y clasificación pendiente.

## 5. Campos y evidencia

Cada campo mostrará valor extraído, confianza, valor corregido, fuente, página, advertencia y criticidad. Se distinguirán visualmente los valores confirmados, dudosos, bloqueantes, ausentes/no aplicables y calculados o internos.

La fecha de vencimiento seguirá esta prioridad:

1. fecha explícita de la factura;
2. condición explícita, como `Net 30`;
3. contrato, OC o maestro;
4. plazo legal aplicable;
5. fecha de emisión + 30 días naturales como estimación operativa, identificando la fuente y la regla aplicada.

La ausencia de vencimiento en el PDF no genera revisión por sí sola.

## 6. Campos editables

Se podrán confirmar o corregir tipo documental, proveedor, Tax ID, cliente, número, fechas, condición de pago, moneda, importes, OC, proyecto, método de pago y datos bancarios visibles en el documento.

Una corrección de datos bancarios extraídos no modificará el maestro. Los cambios del maestro mantendrán un circuito separado con doble aprobación.

## 7. Motivos de revisión

Los motivos se explicarán en lenguaje de negocio y no solamente mediante códigos técnicos: extracción incompleta, clasificación dudosa, incoherencia de importes, conflicto con OC o maestro, duplicado, dato bancario inconsistente, documento ilegible, entre otros.

## 8. Regla PO / non-PO

La ausencia de OC es una ruta normal y no genera revisión automática. El sistema deberá indicar que la factura sigue la ruta non-PO.

Sí generan revisión los conflictos con una OC mencionada: inexistencia, proveedor incorrecto, moneda diferente, falta de saldo, estado cerrado, líneas o importes fuera de tolerancia, o referencia ambigua.

Principio aprobado:

> Sin OC es una ruta operativa. Conflicto con OC es una excepción.

## 9. Acciones humanas

La pantalla permitirá guardar borrador, confirmar valores, corregir y continuar, solicitar información, rechazar documento y escalar una excepción. Rechazar o escalar exigirá motivo.

## 10. Efecto de una corrección

Toda corrección conservará el valor original, registrará valor corregido, usuario, fecha y motivo; identificará controles afectados; reejecutará los controles correspondientes; recalculará la ruta; e invalidará aprobaciones anteriores cuando cambie un campo crítico.

Corregir o confirmar información nunca libera un pago.

## 11. Separación respecto de Cola de Excepciones

**Revisión Humana** confirma extracción, clasificación y datos internos. **Cola de Excepciones** investiga y resuelve incumplimientos de controles.

Un documento puede pasar por Revisión Humana y, después de reejecutar controles, derivarse a la Cola de Excepciones.

## Dependencia de implementación

Antes de implementar este diseño debe cerrarse y validarse el hotfix de Fase 5.1 que conecta el despacho real de la API con Celery cuando existe broker. No se debe comenzar Fase 6 sobre un contrato `202` que todavía procese inline.
