# popc.py
from __future__ import annotations
import math
import numpy as np
from dataclasses import dataclass, field
from typing import Dict, Set, Tuple, TYPE_CHECKING

if TYPE_CHECKING:
    import simpy
    import networkx as nx
    from components import PeerToPeerOverlay, LedgerTransaction, IoTGateway

# Configuration parameters for the PoPC protocol.
@dataclass
class PoPCConfiguration:
    base_tolerance_kw: float = 0.3
    power_magnitude_scaling: float = 0.02
    flow_magnitude_scaling: float = 0.02
    attestation_window_s: float = 0.30
    num_two_hop_witnesses: int = 2
    colluding_bus_ids: Set[int] = field(default_factory=set)
    start_after_slot: int = 1

# Implements the core logic of the Proof of Physical Consistency protocol.
class ProofOfPhysicalConsistencyManager:
    def __init__(self, env: simpy.Environment, electrical_graph: nx.Graph, overlay: PeerToPeerOverlay, config: PoPCConfiguration):
        self.env, self.electrical_graph, self.overlay, self.config = env, electrical_graph, overlay, config
        self.power_observed_mw: Dict[Tuple[int, int], float] = {}
        self.flow_observed_mw: Dict[Tuple[Tuple[int, int], int], float] = {}
        self.transaction_info: Dict[str, dict] = {}
        self.overlay.popc_manager = self

    # Determines the number of attestations required for a transaction to be confirmed.
    def _get_required_quorum(self, origin_bus_id: int) -> int:
        degree = self.electrical_graph.degree(origin_bus_id)
        return max(1, degree) if degree <= 3 else math.ceil(0.75 * degree)

    # Registers a new transaction proposal and starts its attestation timer.
    def register_proposal(self, transaction: LedgerTransaction):
        o, s = transaction.bus_id, transaction.slot
        p_mw = float(transaction.payload.get("net_power_reported_mw", 0.0))
        self.power_observed_mw[(o, s)] = p_mw
        for nb_str, val in transaction.payload.get("line_flows_mw", {}).items():
            self.flow_observed_mw[((o, int(nb_str)), s)] = float(val)

        self.transaction_info[transaction.transaction_id] = {
            "origin_bus_id": o, "slot": s, "attack_flags": transaction.payload.get("attack_flags", []),
            "proposal_timestamp": self.env.now, "is_confirmed": False, "is_rejected": False,
            "decision_timestamp": -1.0, "approvals_informed": set(), "approvals_uninformed": set(),
            "rejections": {}, "required_quorum": self._get_required_quorum(o)
        }
        self.env.process(self._schedule_timeout(transaction.transaction_id))

    def _schedule_timeout(self, tx_id: str):
        yield self.env.timeout(self.config.attestation_window_s)
        info = self.transaction_info.get(tx_id)
        if not info or info["is_confirmed"] or info["is_rejected"]: return
        approvals = len(info["approvals_informed"]) + len(info["approvals_uninformed"])
        if len(info["rejections"]) == 0 and (info["slot"] < self.config.start_after_slot or approvals >= info["required_quorum"]):
            self._confirm_transaction(tx_id)
        else:
            self._reject_transaction(tx_id)

    # Handles a received transaction from the perspective of an attester.
    def on_receive_transaction(self, attester_bus_id: int, transaction: LedgerTransaction):
        if attester_bus_id in self.config.colluding_bus_ids:
            self.overlay.send_approval(attester_bus_id, transaction.bus_id, transaction.transaction_id, is_informed=False)
        elif transaction.slot < self.config.start_after_slot:
            self.overlay.send_approval(attester_bus_id, transaction.bus_id, transaction.transaction_id, is_informed=False)
        else:
            decision, evidence = self._perform_physical_consistency_check(attester_bus_id, transaction)
            if decision == "reject":
                self.overlay.send_rejection(attester_bus_id, transaction.bus_id, transaction.transaction_id, evidence)
            elif decision == "approve":
                self.overlay.send_approval(attester_bus_id, transaction.bus_id, transaction.transaction_id, is_informed=True)
    
    # Retrieves the most up-to-date power value for a given bus and slot.
    def _get_latest_power_mw(self, bus_id: int, slot: int) -> Tuple[bool, float]:
        val = self.power_observed_mw.get((bus_id, slot))
        return val is not None, val if val is not None else 0.0

    def _get_latest_flow_mw(self, u: int, v: int, slot: int) -> Tuple[bool, float]:
        if (val := self.flow_observed_mw.get(((u, v), slot))) is not None: return True, val
        if (val := self.flow_observed_mw.get(((v, u), slot))) is not None: return True, -val
        return False, 0.0

    # Executes the core Kirchhoff's Current Law (KCL) check for a transaction.
    def _perform_physical_consistency_check(self, attester_bus_id: int, transaction: LedgerTransaction) -> Tuple[str, dict]:
        o, s = transaction.bus_id, transaction.slot
        p_origin = float(transaction.payload.get("net_power_reported_mw", 0.0))
        cut = set([attester_bus_id] + list(self.electrical_graph.neighbors(attester_bus_id)))
        cut.add(o)
        
        injections = [p_origin if n == o else self._get_latest_power_mw(n, s)[1] for n in cut if self._get_latest_power_mw(n, s)[0] or n == o]
        if len(injections) != len(cut): return "abstain", {}

        flows = [self._get_latest_flow_mw(u, v, s)[1] for u in cut for v in self.electrical_graph.neighbors(u) if v not in cut and self._get_latest_flow_mw(u, v, s)[0]]
        if len(flows) != len([1 for u in cut for v in self.electrical_graph.neighbors(u) if v not in cut]): return "abstain", {}

        residual = sum(injections) + sum(flows)
        tolerance = max(self.config.base_tolerance_kw/1000.0, self.config.power_magnitude_scaling * sum(map(abs, injections)) + self.config.flow_magnitude_scaling * sum(map(abs, flows)))

        return ("reject", {"r": residual, "t": tolerance}) if abs(residual) > tolerance else ("approve", {})

    def receive_approval(self, origin_bus_id: int, tx_id: str, attester_bus_id: int, is_informed: bool):
        if (info := self.transaction_info.get(tx_id)) and not info["is_rejected"] and not info["is_confirmed"]:
            info["approvals_informed" if is_informed else "approvals_uninformed"].add(attester_bus_id)

    def receive_rejection(self, origin_bus_id: int, tx_id: str, attester_bus_id: int, evidence: dict):
        if (info := self.transaction_info.get(tx_id)) and not info["is_rejected"] and not info["is_confirmed"]:
            info["rejections"][attester_bus_id] = evidence
            self._reject_transaction(tx_id)

    def _confirm_transaction(self, tx_id: str):
        if (info := self.transaction_info.get(tx_id)) and not info["is_rejected"] and not info["is_confirmed"]:
            info["is_confirmed"] = True
            info["decision_timestamp"] = self.env.now

    def _reject_transaction(self, tx_id: str):
        if (info := self.transaction_info.get(tx_id)) and not info["is_rejected"] and not info["is_confirmed"]:
            info["is_rejected"] = True
            info["decision_timestamp"] = self.env.now