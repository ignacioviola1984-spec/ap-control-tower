"""Contrato de contenido del tab Calidad medida (evals)."""

from __future__ import annotations

import json

from ap_control_tower.ui.trial import quality


class FakeMetricTarget:
    def __init__(self, owner: "FakeStreamlit") -> None:
        self.owner = owner

    def metric(self, label, value, delta=None, **kwargs) -> None:
        self.owner.metrics.append((str(label), str(value), str(delta)))


class FakeStreamlit:
    def __init__(self) -> None:
        self.text: list[str] = []
        self.metrics: list[tuple[str, str, str]] = []

    def title(self, value) -> None:
        self.text.append(str(value))

    def subheader(self, value) -> None:
        self.text.append(str(value))

    def markdown(self, value) -> None:
        self.text.append(str(value))

    def html(self, value) -> None:
        self.text.append(str(value))

    def info(self, value) -> None:
        self.text.append(str(value))

    def dataframe(self, *args, **kwargs) -> None:
        return None

    def columns(self, count):
        return [FakeMetricTarget(self) for _ in range(count)]


def main() -> int:
    path = quality._summary_path()
    assert path is not None
    summary = json.loads(path.read_text(encoding="utf-8"))
    replay = summary["policy_replays"][-1]
    assert replay["id"] == "run3-policy-replay"
    assert replay["llamadas_document_ai"] == 0
    assert replay["ruteo_exactitud_pct"] == 96.9
    assert replay["recall_derivacion_pct"] == 100.0
    assert replay["smoke"]["estado"] == "diferido hasta integrar Zoho/Sage"

    fake = FakeStreamlit()
    original = quality.st
    quality.st = fake
    try:
        quality.render()
    finally:
        quality.st = original

    rendered = "\n".join(fake.text)
    assert "Run3: los fixes revalidados sin reprocesar PDFs" in rendered
    assert "106 extracciones persistidas" in rendered
    assert "0 llamadas a Document AI" in rendered
    assert "Valor comercial de esta evidencia" in rendered
    assert "Qué no demuestra este replay" in rendered
    assert "diferido hasta integrar Zoho/Sage" in rendered
    assert "sin dejar pasar ningún caso de riesgo de pago" not in rendered

    metrics = {(label, value) for label, value, _ in fake.metrics}
    assert ("Exactitud de ruteo", "96.9%") in metrics
    assert ("Recall de derivación", "100.0%") in metrics
    assert ("Falsos negativos", "0") in metrics
    assert ("Revisión humana (11/106)", "10.4%") in metrics

    print("PASS tab evals: run3, valor comercial y limitaciones visibles")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
