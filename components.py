# components.py
from __future__ import annotations
import uuid
import random
from dataclasses import dataclass, field
from typing import Dict, List, Set, Optional

import simpy
import networkx as nx

from utils import create_signature, verify_signature
from adversary import AdversaryController
from physical_models import DistributedEnergyResourceProfile, SmartMeter, BatteryPolicy
from popc import ProofOfPhysicalConsistencyManager

# Represents a signed transaction on the DAG ledger.
@dataclass
class LedgerTransaction:
    transaction_id: str
    bus_id: int
    slot: int
    payload: dict
    parent_tx_ids: List[str]
    signature: str

# Manages the Directed Acyclic Graph (DAG) ledger state.
class DirectedAcyclicGraphLedger:
    def __init__(self):
        self.ledger_graph = nx.DiGraph()
        self.tips: Set[str] = set()

    def get_tips(self, num_tips: int = 2) -> List[str]:
        if not self.tips: return []
        return random.sample(list(self.tips), k=min(num_tips, len(self.tips)))

    def add_transaction(self, transaction: LedgerTransaction):
        if transaction.transaction_id in self.ledger_graph: return
        self.ledger_graph.add_node(transaction.transaction_id, data=transaction)
        for parent_id in transaction.parent_tx_ids:
            if parent_id in self.ledger_graph:
                self.ledger_graph.add_edge(transaction.transaction_id, parent_id)
                self.tips.discard(parent_id)
        self.tips.add(transaction.transaction_id)

# Manages the simulated peer-to-peer communication network.
class PeerToPeerOverlay:
    def __init__(self, env: simpy.Environment, gateways: Dict[int, "IoTGateway"], ledger: DirectedAcyclicGraphLedger, num_two_hop_witnesses: int):
        self.env = env
        self.gateways = gateways
        self.ledger = ledger
        self.num_two_hop_witnesses = num_two_hop_witnesses
        self.popc_manager: Optional[ProofOfPhysicalConsistencyManager] = None
        for bus_id, gateway in gateways.items():
            env.process(gateway.run_message_loop(self))

    def _schedule_delivery(self, action):
        delay = random.uniform(5.0, 40.0) / 1000.0
        self.env.process(self._delivery_process(delay, action))

    def _delivery_process(self, delay_s: float, action):
        yield self.env.timeout(delay_s)
        action()
        
    def broadcast_transaction(self, origin_bus_id: int, transaction: LedgerTransaction):
        self.ledger.add_transaction(transaction)
        if self.popc_manager: self.popc_manager.register_proposal(transaction)
        
        recipients = set(self.gateways[origin_bus_id].electrical_neighbors)
        direct_neighbors = set(self.gateways[origin_bus_id].electrical_neighbors)
        candidates = set(nb2 for nb in direct_neighbors for nb2 in self.gateways[nb].electrical_neighbors) - direct_neighbors - {origin_bus_id}
        
        recipients.update(random.sample(list(candidates), k=min(self.num_two_hop_witnesses, len(candidates))))
        
        for recipient_id in recipients:
            if random.random() < 0.01: continue # Packet loss
            self._schedule_delivery(lambda r=recipient_id: self.gateways[r].inbox.put({"type": "transaction", "transaction": transaction}))

    def send_approval(self, attester_bus_id: int, origin_bus_id: int, transaction_id: str, is_informed: bool):
        self._schedule_delivery(lambda: self.popc_manager.receive_approval(origin_bus_id, transaction_id, attester_bus_id, is_informed))

    def send_rejection(self, attester_bus_id: int, origin_bus_id: int, transaction_id: str, evidence: dict):
        self._schedule_delivery(lambda: self.popc_manager.receive_rejection(origin_bus_id, transaction_id, attester_bus_id, evidence))

# Represents a prosumer's IoT gateway, managing DERs and communication.
class IoTGateway:
    def __init__(self, env: simpy.Environment, bus_id: int, der_profile: DistributedEnergyResourceProfile, meter: SmartMeter, policy: Optional[BatteryPolicy]):
        self.env = env
        self.bus_id = bus_id
        self.der_profile = der_profile
        self.meter = meter
        self.policy = policy
        
        # Communication and State Attributes
        self.inbox: simpy.Store = simpy.Store(env)
        self.electrical_neighbors: List[int] = []
        self.prepared_transaction: Optional[LedgerTransaction] = None
        
        # Physical State Attributes
        self.soc_kwh = max(0.0, der_profile.battery_capacity_kwh * random.uniform(0.35, 0.8))
        self.last_true_power_mw: float = 0.0
        self.last_flows_to_neighbors_mw: Dict[int, float] = {}
        self._last_true_generation_mw: float = 0.0
        self._last_true_load_mw: float = 0.0
        
        # Simulation Linkage Attributes (initialized by simulator)
        self.load_indices: List[int] = []
        self.pv_sgen_index: Optional[int] = None
        self.battery_sgen_index: Optional[int] = None
        self.attack_sgen_index: Optional[int] = None
        self.adversary_controller: Optional[AdversaryController] = None

    def update_state_of_charge(self, battery_power_kw: float, slot_duration_hours: float):
        eff = self.der_profile.round_trip_efficiency
        if battery_power_kw > 0:
            self.soc_kwh -= (battery_power_kw * slot_duration_hours) / eff
        else:
            self.soc_kwh += (-battery_power_kw) * slot_duration_hours * eff
        self.soc_kwh = max(0.0, min(self.der_profile.battery_capacity_kwh, self.soc_kwh))

    def run_message_loop(self, overlay: PeerToPeerOverlay):
        while True:
            msg = yield self.inbox.get()
            if msg["type"] == "transaction":
                tx: LedgerTransaction = msg["transaction"]
                p_bytes = f"{tx.bus_id}|{tx.slot}|{tx.parent_tx_ids}|{tx.payload}".encode()
                if verify_signature(tx.bus_id, p_bytes, tx.signature) and overlay.popc_manager:
                    overlay.popc_manager.on_receive_transaction(self.bus_id, tx)

    def prepare_measurement_transaction(self, slot: int, overlay: PeerToPeerOverlay):
        true_gen_mw, true_load_mw = self._last_true_generation_mw, self._last_true_load_mw
        if self.adversary_controller:
            rep_gen_mw, rep_load_mw, flags = self.adversary_controller.alter_reported_power(self.bus_id, slot, true_gen_mw, true_load_mw)
        else:
            rep_gen_mw, rep_load_mw, flags = true_gen_mw, true_load_mw, []

        payload = {
            "net_power_true_mw": round(self.last_true_power_mw, 6),
            "net_power_reported_mw": round(rep_gen_mw - rep_load_mw, 6),
            "attack_flags": flags,
            "line_flows_mw": {str(nb): round(val, 6) for nb, val in self.last_flows_to_neighbors_mw.items()}
        }

        parent_tx_ids = overlay.ledger.get_tips()
        tx_id = str(uuid.uuid4())
        payload_bytes = f"{self.bus_id}|{slot}|{parent_tx_ids}|{payload}".encode()
        self.prepared_transaction = LedgerTransaction(tx_id, self.bus_id, slot, payload, parent_tx_ids, create_signature(self.bus_id, payload_bytes))
        if self.adversary_controller: self.adversary_controller.cache_payload(self.bus_id, payload)
    
    def broadcast_prepared_transaction(self, overlay: PeerToPeerOverlay):
        if self.prepared_transaction:
            overlay.broadcast_transaction(self.bus_id, self.prepared_transaction)
            self.prepared_transaction = None