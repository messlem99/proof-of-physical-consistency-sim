# simulation.py
from __future__ import annotations
import random
from dataclasses import dataclass
from typing import Dict, List

import simpy
import numpy as np
import pandapower as pp

from utils import get_scenario_parameters, initialize_ieee_33_bus_network, create_electrical_graph_from_pandapower
from physical_models import SolarIrradianceModel, ResidentialLoadModel, PriceModel, DistributedEnergyResourceProfile, SmartMeter, SelfConsumptionPolicy, ProfitMaxPolicy
from adversary import AdversaryController, AttackSpecification
from components import IoTGateway, PeerToPeerOverlay, DirectedAcyclicGraphLedger
from popc import ProofOfPhysicalConsistencyManager, PoPCConfiguration

# Simulates the physical power grid, executing power flow calculations.
class PowerGridSimulator:
    def __init__(self, env: simpy.Environment, net: pp.pandapowerNet, gateways: Dict[int, IoTGateway], overlay: PeerToPeerOverlay,
                 solar: SolarIrradianceModel, load: ResidentialLoadModel, price: PriceModel, adversary: AdversaryController):
        self.env, self.net, self.gateways, self.overlay = env, net, gateways, overlay
        self.solar, self.load, self.price, self.adversary = solar, load, price, adversary

        for idx, row in self.net.load.iterrows():
            gateways[int(row.bus)].load_indices.append(idx)
        for bus_id, gw in gateways.items():
            if gw.der_profile.pv_peak_kw > 0: gw.pv_sgen_index = pp.create_sgen(net, bus=bus_id, p_mw=0.0)
            if gw.der_profile.battery_peak_kw > 0: gw.battery_sgen_index = pp.create_sgen(net, bus=bus_id, p_mw=0.0)
            gw.attack_sgen_index = pp.create_sgen(net, bus=bus_id, p_mw=0.0)

    def _update_gateway_line_flows(self):
        for idx, line in self.net.line.iterrows():
            u, v = int(line.from_bus), int(line.to_bus)
            if u in self.gateways and v in self.gateways:
                self.gateways[u].last_flows_to_neighbors_mw[v] = float(self.net.res_line.p_from_mw.at[idx])
                self.gateways[v].last_flows_to_neighbors_mw[u] = float(self.net.res_line.p_to_mw.at[idx])

    # Executes one time slot of the physical simulation, following the phased approach.
    def simulate_time_slot(self, slot: int, slot_duration_hours: float):
        self.load.apply_load_profile(self.net, slot)
        price = self.price.get_price(slot)
        
        # Phase 1: Update physical state for all gateways based on models.
        for bus_id, gw in self.gateways.items():
            pv_kw = gw.der_profile.pv_peak_kw and self.solar.get_pv_power_kw(slot, gw.der_profile.pv_peak_kw, tilt_deg=25) or 0.0
            if gw.pv_sgen_index is not None: self.net.sgen.p_mw.at[gw.pv_sgen_index] = pv_kw / 1000.0

            load_kw = sum(self.net.load.p_mw.at[i] for i in gw.load_indices) * 1000.0
            
            batt_kw = 0.0
            if gw.battery_sgen_index is not None and gw.policy:
                batt_kw = gw.policy.decide_power_kw(load_kw=load_kw, pv_kw=pv_kw, price=price, soc_kwh=gw.soc_kwh, 
                    capacity_kwh=gw.der_profile.battery_capacity_kwh, power_limit_kw=gw.der_profile.battery_peak_kw, slot_duration_h=slot_duration_hours)
                batt_kw = np.clip(batt_kw, -gw.der_profile.battery_peak_kw, gw.der_profile.battery_peak_kw)
                self.net.sgen.p_mw.at[gw.battery_sgen_index] = batt_kw / 1000.0
                gw.update_state_of_charge(batt_kw, slot_duration_hours)

            attack_kw = self.adversary.get_actuation_delta_kw(bus_id, slot)
            self.net.sgen.p_mw.at[gw.attack_sgen_index] = attack_kw / 1000.0
            
            gw._last_true_generation_mw = (max(0, pv_kw) + max(0, batt_kw) + max(0, attack_kw)) / 1000.0
            gw._last_true_load_mw = (load_kw + max(0, -batt_kw) + max(0, -attack_kw)) / 1000.0
        
        # Phase 2: Run power flow for the entire grid to get a consistent physical state.
        pp.runpp(self.net)
        self._update_gateway_line_flows()

        # Phase 3: Prepare all transactions based on the solved grid state.
        for bus_id, gw in self.gateways.items():
            gw.last_true_power_mw = float(self.net.res_bus.p_mw.at[bus_id])
            gw.prepare_measurement_transaction(slot, self.overlay)
        
        # Phase 4: Broadcast all prepared transactions to trigger attestations.
        for gw in self.gateways.values():
            gw.broadcast_prepared_transaction(self.overlay)

# Top-level class that orchestrates the entire simulation model.
@dataclass
class SimulationModel:
    env: simpy.Environment
    gateways: Dict[int, IoTGateway]
    grid_simulator: PowerGridSimulator
    popc_manager: ProofOfPhysicalConsistencyManager

    @staticmethod
    def initialize(*, seed: int, slot_duration_hours: float, der_level: str, popc_config: PoPCConfiguration) -> "SimulationModel":
        random.seed(seed); np.random.seed(seed + 100)
        env = simpy.Environment()
        net = initialize_ieee_33_bus_network()
        electrical_graph = create_electrical_graph_from_pandapower(net)
        params = get_scenario_parameters(der_level)
        bus_ids = sorted(electrical_graph.nodes())

        pv_by_bus = {b: random.uniform(3., 8.) for b in random.sample(bus_ids, k=int(len(bus_ids) * 0.7))}
        batt_by_bus = {b: (pv * 0.6, pv * 2.8) for b, pv in pv_by_bus.items() if random.random() < 0.8}

        gateways = {}
        for bus_id in bus_ids:
            pv_kw = pv_by_bus.get(bus_id, 0.0)
            p_kw, e_kwh = batt_by_bus.get(bus_id, (0.0, 0.0))
            der = DistributedEnergyResourceProfile(pv_kw, p_kw, e_kwh)
            policy = None
            if e_kwh > 0:
                p_name = np.random.choice(list(params["policy_mix"].keys()), p=list(params["policy_mix"].values()))
                if p_name == "self_consumption": policy = SelfConsumptionPolicy()
                elif p_name == "profit_max": policy = ProfitMaxPolicy()
            gateways[bus_id] = IoTGateway(env, bus_id, der, SmartMeter(bus_id), policy)
            gateways[bus_id].electrical_neighbors = list(electrical_graph.neighbors(bus_id))

        slots_per_day = int(round(24 / slot_duration_hours))
        attack_specs = SimulationModel._generate_random_attacks(bus_ids, slots_per_day, seed, popc_config.start_after_slot)
        adversary = AdversaryController(attack_specs, popc_config.start_after_slot)
        for gw in gateways.values(): gw.adversary_controller = adversary

        overlay = PeerToPeerOverlay(env, gateways, DirectedAcyclicGraphLedger(), popc_config.num_two_hop_witnesses)
        popc = ProofOfPhysicalConsistencyManager(env, electrical_graph, overlay, popc_config)
        grid = PowerGridSimulator(env, net, gateways, overlay, SolarIrradianceModel(slots_per_day, seed+200),
                                  ResidentialLoadModel(net, slots_per_day, seed+300), PriceModel(slots_per_day), adversary)

        return SimulationModel(env, gateways, grid, popc)

    @staticmethod
    def _generate_random_attacks(bus_ids: List[int], spd: int, seed: int, min_slot: int) -> List[AttackSpecification]:
        random.seed(seed + 500)
        attacks = []
        for bus_id in random.sample(bus_ids[1:], k=min(6, len(bus_ids)//8)):
            for _ in range(random.randint(2, 4)):
                start = random.randint(max(min_slot, int(0.15*spd)), int(0.90*spd))
                kind = random.choice(["energy_theft", "fraud_gen", "replay"])
                params = {"fraction": random.uniform(0.5, 0.85)} if kind == "energy_theft" else \
                         {"added_kw": random.uniform(3.0, 6.0)} if kind == "fraud_gen" else {}
                attacks.append(AttackSpecification(f"{kind}_{bus_id}_{start}", bus_id, kind, start, start + random.randint(2,6), params))
        return attacks

    def run(self, num_slots: int, slot_step_time_s: float):
        self.env.process(self._driver(num_slots, slot_step_time_s))
        self.env.run()

    def _driver(self, num_slots: int, slot_step_time_s: float):
        for s in range(num_slots):
            self.grid_simulator.simulate_time_slot(s, 15/60)
            yield self.env.timeout(slot_step_time_s)
    
    def print_simulation_results(self):
        print("\n" + "="*50)
        print("=Proof of Physical Consistency (PoPC) Simulation Results")
        print("="*50)

        true_positives, false_negatives, total_attacks = 0, 0, 0
        for info in self.popc_manager.transaction_info.values():
            if info["attack_flags"]:
                total_attacks += 1
                if info["is_rejected"]:
                    true_positives += 1
                elif info["is_confirmed"]:
                    false_negatives += 1
        
        print("\n## Detection Performance\n" + "-"*25)
        print(f"Total Attacks Attempted: {total_attacks}")
        print(f"  - Attacks Detected (TP): {true_positives}")
        print(f"  - Attacks Missed (FN):   {false_negatives}")
        print("\n" + "="*50)