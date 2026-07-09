"""Audit trail inmutable por corrida, con encadenamiento de hashes.

Cada evento registra: agente, accion, factura, control, resultado, evidencia,
timestamp, run_id y commit hash. El hash de cada evento incluye el hash del
anterior: alterar un evento rompe la cadena (patron portado de las demos CFO).
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


def _canonical(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, default=str)


@dataclass
class AuditEvent:
    seq: int
    ts: str
    run_id: str
    commit: str
    agent: str
    action: str
    invoice_id: str | None
    control_id: str | None
    result: str | None
    evidence: dict[str, Any]
    prev_hash: str
    hash: str = ""

    def compute_hash(self) -> str:
        payload = _canonical(
            {
                "seq": self.seq,
                "ts": self.ts,
                "run_id": self.run_id,
                "commit": self.commit,
                "agent": self.agent,
                "action": self.action,
                "invoice_id": self.invoice_id,
                "control_id": self.control_id,
                "result": self.result,
                "evidence": self.evidence,
                "prev_hash": self.prev_hash,
            }
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class AuditTrail:
    GENESIS = "0" * 64

    def __init__(self, run_id: str | None = None, commit: str = "sin-git") -> None:
        self.run_id = run_id or f"run-{uuid.uuid4().hex[:12]}"
        self.commit = commit
        self.events: list[AuditEvent] = []

    def add(
        self,
        agent: str,
        action: str,
        invoice_id: str | None = None,
        control_id: str | None = None,
        result: str | None = None,
        evidence: dict[str, Any] | None = None,
    ) -> AuditEvent:
        prev = self.events[-1].hash if self.events else self.GENESIS
        ev = AuditEvent(
            seq=len(self.events) + 1,
            ts=datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            run_id=self.run_id,
            commit=self.commit,
            agent=agent,
            action=action,
            invoice_id=invoice_id,
            control_id=control_id,
            result=result,
            evidence=evidence or {},
            prev_hash=prev,
        )
        ev.hash = ev.compute_hash()
        self.events.append(ev)
        return ev

    def verify_chain(self) -> bool:
        prev = self.GENESIS
        for ev in self.events:
            if ev.prev_hash != prev or ev.compute_hash() != ev.hash:
                return False
            prev = ev.hash
        return True

    def to_jsonl(self, path: str) -> None:
        with open(path, "w", encoding="utf-8") as f:
            for ev in self.events:
                f.write(_canonical(ev.__dict__) + "\n")
