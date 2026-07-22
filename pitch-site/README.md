# AP Control Tower · Sales Microsite

Micrositio comercial privado y sin marcas corporativas, con casos anonimizados.

## Contenido

- Business Case y calculador de oportunidad con supuestos visibles.
- Flujo operativo end-to-end.
- Casos de uso, evidencia de la demo y arquitectura.
- Enlaces a la Demo y al PoC desplegados en Cloud Run.

## Acceso privado en Netlify

El Edge Function `protect` bloquea todo el sitio si la variable de entorno
`AP_PITCH_PASSWORD` no está configurada. La contraseña no debe guardarse en el
repositorio ni en este directorio.

Configuración en Netlify:

1. Usar `pitch-site` como Base directory y Publish directory.
2. Crear `AP_PITCH_PASSWORD` en Project configuration > Environment variables.
3. Desplegar y verificar acceso, logout, headers `noindex` y enlaces externos.

La sesión usa una cookie `HttpOnly`, `Secure`, `SameSite=Strict` con vigencia de
24 horas. `robots.txt`, la meta robots y el header `X-Robots-Tag` bloquean la
indexación; no sustituyen la autenticación.
