"""Registro de componentes CCv2 resistente al ciclo de vida del runtime.

Los ejemplos declaran el componente a nivel de módulo. Eso alcanza mientras el
proceso tenga un único runtime vivo, pero el registro de definiciones cuelga del
runtime activo (``Runtime.instance().bidi_component_registry``), no del módulo:
si el módulo ya está en ``sys.modules`` cuando aparece un registro nuevo, la
declaración no vuelve a ejecutarse y el montaje falla con
``Component '<nombre>' is not registered``. Pasa al navegar entre páginas bajo
``AppTest`` y en cualquier reinicio de runtime dentro del mismo proceso.

Registrar en cada ejecución es seguro: el registro sobrescribe por nombre y solo
avisa cuando la definición **cambia**. Como acá las definiciones son constantes
del módulo, volver a registrar es idempotente.
"""

from __future__ import annotations

import streamlit as st


def component(name: str, *, html: str, css: str | None = None,
              js: str | None = None):
    """Devuelve el montador del componente, asegurando su registro.

    Se llama en cada ejecución, justo antes de montar.
    """
    return st.components.v2.component(name, html=html, css=css, js=js)


__all__ = ["component"]
