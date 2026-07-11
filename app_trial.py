"""Entrypoint delgado de la app "Prueba con tus facturas" (modo trial).

Ejecutar:  streamlit run app_trial.py
Equivalente a correr app.py con AP_APP_MODE=trial. Misma imagen, mismo paquete,
mismo password (AP_DEMO_PASSWORD). Session-only: nada persiste fuera de la sesion.
"""

from __future__ import annotations

from ap_control_tower.ui import bootstrap

bootstrap.run("trial")
