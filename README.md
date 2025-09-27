# proof-of-physical-consistency-sim
A Python-based simulation framework for the "Proof of Physical Consistency" (PoPC) pre-consensus protocol, designed to mitigate False Data Injection (FDI) attacks in decentralized P2P energy trading markets.

# A Physics-Aware Pre-Consensus Protocol for False Data Injection Mitigation in DAG-Based Peer-to-Peer Energy Trading

### Simulation Framework for the PoPC Protocol

![Python Version](https://img.shields.io/badge/python-3.9%2B-blue.svg)
![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)

This repository contains the official simulation code for the research paper, "A Physics-Aware Pre-Consensus Protocol for False Data Injection Mitigation in DAG-Based Peer-to-Peer Energy Trading." The framework is built in Python using SimPy for discrete-event simulation and Pandapower for power flow analysis.

## Abstract

Peer-to-peer (P2P) energy trading over distributed ledgers inherits a data-integrity risk from edge metering, as consensus verifies signatures and order, not physics. This work introduces **Proof of Physical Consistency (PoPC)**, a physics-aware, neighbor-attested pre-consensus for Directed Acyclic Graph (DAG) ledgers that binds transaction eligibility to one-hop electrical checks executed within the proposal window. This simulation evaluates the PoPC protocol on the IEEE 33-bus test feeder against a variety of False Data Injection (FDI) attack scenarios.

---

## File Structure

The project is organized into several modules for clarity and maintainability:

* `run_simulation.py`: **Main entry point.** Configure and launch the simulation from this file.
* `simulation.py`: The high-level simulation engine that orchestrates the entire model.
* `popc.py`: Contains the core implementation of the Proof of Physical Consistency protocol.
* `components.py`: Defines the core cyber components of the system, including the `IoTGateway`, `PeerToPeerOverlay`, and `DirectedAcyclicGraphLedger`.
* `adversary.py`: Implements the threat model, including specifications for different FDI attack types.
* `physical_models.py`: Contains all classes that model the physical world, such as solar generation, residential loads, DERs, and electricity prices.
* `utils.py`: Includes helper functions for creating signatures and setting up the Pandapower network.

---

## Installation

The simulation requires Python 3.9+ and several scientific computing libraries.

1.  **Clone the repository:**
    ```sh
    git clone [https://github.com/messlem99/proof-of-physical-consistency-sim.git](https://github.com/messlem99/proof-of-physical-consistency-sim.git)
    cd proof-of-physical-consistency-sim
    ```

2.  **Install the required packages:**
    It's recommended to use a virtual environment.
    ```sh
    # Create and activate a virtual environment (optional but recommended)
    python -m venv venv
    source venv/bin/activate  # On Windows, use `venv\Scripts\activate`

    # Install dependencies
    pip install numpy simpy networkx pandapower
    ```

---

## How to Run

All simulation parameters (e.g., random seed, DER penetration level, PoPC configuration) are located in the `run_simulation.py` file.

To execute the simulation, simply run this file from your terminal:

```sh
python run_simulation.py
```

---

## Expected Output

After running, the script will print a summary of the detection performance to the console.

```
Starting simulation for 96 slots (1 day(s))...
Simulation finished in 10.70 seconds.

==================================================
Proof of Physical Consistency (PoPC) Simulation Results
==================================================

## Detection Performance
-------------------------
Total Attacks Attempted: 41
  - Attacks Detected (TP): 41
  - Attacks Missed (FN):   0

==================================================
```

---

## How to Cite

If you use this code in your research, please cite our paper:

```bibtex
@article{messlem2025popc,
  author    = {Messlem, Abdelkader and Messlem, Youcef},
  title     = {A Physics-Aware Pre-Consensus Protocol for False Data Injection Mitigation in DAG-Based Peer-to-Peer Energy Trading},
  journal   = {[TODO: Add Journal Name]},
  year      = {[TODO: Add Year]},
  volume    = {[TODO: Add Volume]},
  pages     = {[TODO: Add Pages]},
  doi       = {[TODO: Add DOI]}
}
```

---

## License

This project is licensed under the MIT License. See the `LICENSE` file for details.
