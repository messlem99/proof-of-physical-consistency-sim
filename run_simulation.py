# run_simulation.py
import time
from simulation import SimulationModel
from popc import PoPCConfiguration

def main():
    """
    Main execution function to configure and run the PoPC simulation.
    This is the single entry point for the simulation.
    """
    # --- Simulation Parameters ---
    SIMULATION_SEED = 123
    DER_PENETRATION_LEVEL = "high"
    SLOT_DURATION_HOURS = 0.25
    SIMULATION_DAYS = 1
    
    # --- PoPC Protocol Configuration ---
    popc_protocol_config = PoPCConfiguration(
        base_tolerance_kw=0.3,
        power_magnitude_scaling=0.02,
        flow_magnitude_scaling=0.02,
        attestation_window_s=0.30,
        num_two_hop_witnesses=2,
        start_after_slot=1
    )

    # --- Model Initialization ---
    simulation = SimulationModel.initialize(
        seed=SIMULATION_SEED,
        slot_duration_hours=SLOT_DURATION_HOURS,
        der_level=DER_PENETRATION_LEVEL,
        popc_config=popc_protocol_config
    )

    # --- Simulation Execution ---
    total_slots = int(round((24 / SLOT_DURATION_HOURS) * SIMULATION_DAYS))
    print(f"Starting simulation for {total_slots} slots ({SIMULATION_DAYS} day(s))...")
    start_time = time.time()
    
    simulation.run(num_slots=total_slots, slot_step_time_s=0.01)
    
    end_time = time.time()
    print(f"Simulation finished in {end_time - start_time:.2f} seconds.")

    # --- Results ---
    simulation.print_simulation_results()

if __name__ == "__main__":
    main()