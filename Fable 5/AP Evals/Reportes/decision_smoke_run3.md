# Decision de evaluacion · cloud smoke posterior a run3

Fecha: 2026-07-16  
Estado: **diferido hasta integrar Zoho/Sage**

## Decision

No ejecutar ahora el cloud smoke de run3. El replay offline ya valido sobre las
106 extracciones persistidas los cambios de politica que no dependen de una
nueva lectura del PDF: nota de credito, duplicados, casi-duplicados y ruteo a
revision humana.

Un smoke inmediato de cinco documentos confirmaria el deployment de esos fixes,
pero no agregaria evidencia comercial material. Los controles con mayor valor
para la siguiente etapa —autorizacion y saldo de OC, match contra tolerancias y
validacion bancaria— todavia no estan conectados en el trial a los maestros de
Zoho/Sage.

## Evidencia disponible sin cloud smoke

- 106/106 extracciones de run2 reejecutadas offline.
- Exactitud de ruteo: 94,8% en run2 → 96,9% en run3.
- Recall de derivacion: 75% → 100%.
- Falsos negativos del alcance formal: 1 → 0.
- GD-107 casi-duplicado: ahora detectado.
- GD-119 nota de credito: ahora derivada a revision.
- Llamadas a Document AI: 0.

La tasa de revision queda en 11/106 (10,4%), apenas sobre el target de 10%.
El principal trade-off es que el detector de duplicados retiene tambien la
factura original GD-105 hasta que un humano decida cual integrante del grupo es
valido.

## Condiciones para reabrir el smoke

1. Adaptadores Zoho/Sage conectados al circuito evaluable.
2. Maestro de proveedores y datos bancarios accesible con tratamiento seguro.
3. Maestro de OC con autorizacion, estado, importe y saldo disponible.
4. Reglas C3, C5 y C6 conectadas al workflow del PoC.
5. Version desplegable asociada a un commit limpio.
6. Matriz de aceptacion definida: resultado esperado, evidencia y criterio de
   bloqueo por documento.

## Muestra reservada

Quedan versionados 15 candidatos en
`Corridas/run3_cloud_smoke_candidates.csv`: los fixes, todos los controles que
no fueron evaluables offline y tres controles negativos. La lista puede
reducirse si cada integracion se habilita por etapas.

## Claims permitidos mientras el smoke esta diferido

- La politica de ruteo fue revalidada sobre las 106 extracciones persistidas.
- El replay corrigio los dos errores conocidos y recupero 100% de recall en el
  alcance formal de 96 documentos.
- La evidencia se obtuvo sin volver a consumir Document AI.

No afirmar todavia:

- integracion productiva con Zoho/Sage;
- cero escapes para todo el conjunto C2-C9;
- nueva medicion de exactitud del extractor;
- validacion end-to-end de C3, C5 o C6 contra maestros externos.
