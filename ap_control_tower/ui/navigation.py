"""Comportamiento compartido de navegación entre vistas Streamlit."""

from __future__ import annotations

import streamlit as st
import streamlit.components.v1 as components


def scroll_to_top_on_change(choice: str, *, state_key: str) -> None:
    """Lleva el contenido al inicio únicamente cuando cambia la vista."""
    if st.session_state.get(state_key) == choice:
        return

    st.session_state[state_key] = choice
    sequence_key = f"{state_key}_scroll_sequence"
    st.session_state[sequence_key] = st.session_state.get(sequence_key, 0) + 1
    sequence = st.session_state[sequence_key]

    # Se ejecuta al final de la vista. Los reintentos cubren la restauración de
    # posición que Streamlit puede hacer después de reconciliar el DOM.
    components.html(
        f"""<script>/* navigation-scroll-{sequence} */
        (() => {{
          const subir = () => {{
            const doc = window.parent.document;
            const candidates = [
              doc.querySelector('section[data-testid="stMain"]'),
              doc.querySelector('[data-testid="stAppViewContainer"]'),
              doc.scrollingElement,
              doc.documentElement,
              doc.body
            ];
            for (const el of candidates) {{
              if (!el) continue;
              el.scrollTop = 0;
              if (typeof el.scrollTo === 'function') {{
                el.scrollTo({{top: 0, left: 0, behavior: 'auto'}});
              }}
            }}
            window.parent.scrollTo({{top: 0, left: 0, behavior: 'auto'}});
          }};
          [0, 50, 150, 300, 600, 1000].forEach(ms => setTimeout(subir, ms));
        }})();
        </script>""",
        height=0,
    )
