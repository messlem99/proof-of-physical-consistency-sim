# adversary.py
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

# Defines the parameters for a specific FDI attack instance, including its type,
# duration, and intensity. This aligns with the threat model in the paper.
@dataclass
class AttackSpecification:
    name: str
    bus_id: int
    kind: str
    start_slot: int
    end_slot: int
    params: dict = field(default_factory=dict)

# Manages the execution of all scheduled FDI attacks based on the threat model.
# This class is responsible for both data fabrication and malicious actuation attacks.
class AdversaryController:
    def __init__(self, specifications: List[AttackSpecification], min_attack_slot: int = 1):
        self.specifications = specifications
        self.min_attack_slot = min_attack_slot
        self._last_payload_by_bus: Dict[int, dict] = {}

    def get_active_attacks(self, bus_id: int, slot: int) -> List[AttackSpecification]:
        """Checks if a given bus is under a specific attack at a given slot."""
        return [
            spec for spec in self.specifications 
            if spec.bus_id == bus_id and max(spec.start_slot, self.min_attack_slot) <= slot <= spec.end_slot
        ]

    def get_actuation_delta_kw(self, bus_id: int, slot: int) -> float:
        """
        Calculates any malicious physical power injection or consumption (actuation attack).
        This simulates an adversary directly controlling a device to destabilize the grid.
        """
        delta_kw = 0.0
        for spec in self.get_active_attacks(bus_id, slot):
            if spec.kind == "destabilize":
                delta_kw += float(spec.params.get("kw", 0.0))
        return delta_kw

    def alter_reported_power(self, bus_id: int, slot: int, true_gen_mw: float, true_load_mw: float) -> Tuple[float, float, List[str]]:
        """
        Applies data fabrication attacks (e.g., energy theft, fraudulent generation, replay)
        to a measurement report before it is broadcast.
        """
        rep_gen_mw, rep_load_mw, flags = true_gen_mw, true_load_mw, []
        for spec in self.get_active_attacks(bus_id, slot):
            if spec.kind == "energy_theft":
                rep_load_mw *= (1.0 - float(spec.params.get("fraction", 0.6)))
                flags.append("energy_theft")
            elif spec.kind == "fraud_gen":
                rep_gen_mw += float(spec.params.get("added_kw", 4.0)) / 1000.0
                flags.append("fraud_gen")
            elif spec.kind == "replay":
                if (prev_payload := self._last_payload_by_bus.get(bus_id)):
                    rep_gen_mw = prev_payload.get("generation_mw_reported", rep_gen_mw)
                    rep_load_mw = prev_payload.get("load_mw_reported", rep_load_mw)
                    flags.append("replay")
        return rep_gen_mw, rep_load_mw, flags

    def cache_payload(self, bus_id: int, payload: dict):
        """Stores the last broadcasted payload for a given bus for potential future replay attacks."""
        self._last_payload_by_bus[bus_id] = payload