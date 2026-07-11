"""AP Control Tower: punto de entrada Streamlit.

Ejecutar:  streamlit run app.py  (el puerto se pasa por CLI: --server.port)
Requiere la env var AP_DEMO_PASSWORD; sin ella la app no renderiza nada.

El modo se elige con AP_APP_MODE=demo|trial (default: demo, para conservar la URL
y el comportamiento actuales). En modo trial arranca "Prueba con tus facturas".
El arranque compartido (page config, password, tema) vive en ui.bootstrap.
"""

from __future__ import annotations

import os

from ap_control_tower.ui import bootstrap

bootstrap.run(os.environ.get("AP_APP_MODE", "demo"))
