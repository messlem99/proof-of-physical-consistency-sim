# utils.py
import pandapower as pp
import pandapower.networks as pn
import networkx as nx

def create_signature(bus_id: int, payload: bytes) -> str:
    """Creates a simple, non-cryptographic signature for simulation purposes."""
    return f"sig:{bus_id}:{hash(payload)}"

def verify_signature(bus_id: int, payload: bytes, signature: str) -> bool:
    """Verifies a pseudo-signature against a payload."""
    try:
        _, received_bus_id, received_hash = signature.split(":")
        return int(received_bus_id) == bus_id and received_hash == str(hash(payload))
    except Exception:
        return False

def initialize_ieee_33_bus_network() -> pp.pandapowerNet:
    """Loads the IEEE 33-bus test feeder configuration from pandapower."""
    net = pn.case33bw()
    if "name" not in net.bus.columns:
        net.bus["name"] = [f"bus_{i}" for i in net.bus.index]
    return net

def create_electrical_graph_from_pandapower(net: pp.pandapowerNet) -> nx.Graph:
    """Creates a NetworkX graph representing the physical grid topology."""
    electrical_graph = nx.Graph()
    for bus_index in net.bus.index.tolist():
        electrical_graph.add_node(int(bus_index))
    for idx, line in net.line.iterrows():
        u, v = int(line["from_bus"]), int(line["to_bus"])
        electrical_graph.add_edge(u, v)
    for idx, trafo in net.trafo.iterrows():
        hv_bus, lv_bus = int(trafo["hv_bus"]), int(trafo["lv_bus"])
        electrical_graph.add_edge(hv_bus, lv_bus)
    return electrical_graph

def get_scenario_parameters(level: str):
    """Returns a dictionary of parameters based on the desired DER penetration level."""
    level = level.lower()
    if level == "high":
        return dict(policy_mix={"self_consumption": 0.35, "passive": 0.10, "profit_max": 0.50, "none": 0.05})
    else: # Default to medium for simplicity
        return dict(policy_mix={"self_consumption": 0.45, "passive": 0.20, "profit_max": 0.30, "none": 0.05})