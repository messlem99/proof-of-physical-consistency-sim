# physical_models.py
import math
import random
from dataclasses import dataclass
import numpy as np
import pandapower as pp

# Models solar PV generation based on geographic and atmospheric conditions.
class SolarIrradianceModel:
    def __init__(self, slots_per_day: int = 96, cloud_seed: int = 0, lat_deg: float = 36.75, day_of_year: int = 172):
        self.lat = math.radians(lat_deg)
        self.n = day_of_year
        self.slots_per_day = slots_per_day
        self.cloud_profile = self._generate_cloud_profile(seed=cloud_seed)

    def _get_declination(self) -> float:
        return math.radians(23.45) * math.sin(2 * math.pi * (284 + self.n) / 365.0)

    def _get_hour_angle(self, slot: int) -> float:
        hours = (slot / self.slots_per_day) * 24.0
        return math.radians(15.0 * (hours - 12.0))

    def _get_cosine_zenith(self, slot: int) -> float:
        declination = self._get_declination()
        hour_angle = self._get_hour_angle(slot)
        cos_zenith = math.sin(self.lat) * math.sin(declination) + math.cos(self.lat) * math.cos(declination) * math.cos(hour_angle)
        return max(0.0, cos_zenith)

    def _generate_cloud_profile(self, seed: int) -> np.ndarray:
        rng = np.random.RandomState(seed)
        a, sigma, x = 0.92, 0.03, 0.85
        profile = [np.clip(a * (x := x) + (1-a)*0.85 + rng.randn()*sigma, 0.6, 1.05) for _ in range(self.slots_per_day)]
        return np.array(profile)

    def get_pv_power_kw(self, slot: int, nameplate_kw: float, tilt_deg: float, system_efficiency: float = 0.9) -> float:
        cos_zenith = self._get_cosine_zenith(slot)
        if cos_zenith <= 0: return 0.0
        ghi = 1098.0 * cos_zenith * math.exp(-0.057 / max(0.05, cos_zenith)) * self.cloud_profile[slot % self.slots_per_day]
        dhi = 0.2 * ghi
        dni = max(0.0, (ghi - dhi) / max(1e-6, cos_zenith))
        beam = dni * max(0.0, cos_zenith * math.cos(math.radians(tilt_deg)))
        diffuse = dhi * (1 + math.cos(math.radians(tilt_deg))) / 2.0
        power_kw = nameplate_kw * (beam + diffuse) / 1000.0 * system_efficiency
        return max(0.0, power_kw)

# Generates realistic, time-varying residential load profiles.
class ResidentialLoadModel:
    def __init__(self, net: pp.pandapowerNet, slots_per_day: int = 96, seed: int = 0):
        self.slots_per_day = slots_per_day
        self.base_p_mw = net.load["p_mw"].copy()
        self.rng = np.random.RandomState(seed)
        self.bus_alpha = 0.9 + 0.2 * self.rng.rand(len(net.load))
        self.spike_probability = 0.02 + 0.03 * self.rng.rand(len(net.load))

    def get_daily_shape(self, slot: int) -> float:
        s = (slot % self.slots_per_day) / self.slots_per_day
        morning = math.exp(-((s - 0.27)**2) / 0.002) * 0.35
        evening = math.exp(-((s - 0.83)**2) / 0.0025) * 0.65
        return max(0.3, 0.55 + morning + evening - 0.10*math.exp(-((s-0.50)**2)/0.01) + np.random.uniform(-0.03, 0.03))

    def apply_load_profile(self, net: pp.pandapowerNet, slot: int):
        shape_factor = self.get_daily_shape(slot)
        for i in range(len(self.base_p_mw)):
            factor = shape_factor * self.bus_alpha[i]
            if self.rng.rand() < self.spike_probability[i]:
                factor += self.rng.uniform(0.05, 0.20)
            # FIX: Use .loc for safe, explicit assignment to avoid ChainedAssignmentError
            net.load.loc[net.load.index[i], 'p_mw'] = self.base_p_mw.iloc[i] * factor

# Defines the physical characteristics of a prosumer's DER assets.
@dataclass
class DistributedEnergyResourceProfile:
    pv_peak_kw: float = 0.0
    battery_peak_kw: float = 0.0
    battery_capacity_kwh: float = 0.0
    round_trip_efficiency: float = 0.92

# Models a smart meter with measurement noise.
@dataclass
class SmartMeter:
    bus_id: int
    def measure(self, true_power_kw: float, true_voltage_pu: float):
        return max(true_power_kw + random.gauss(0.0, 0.01), 0.0), max(true_voltage_pu + random.gauss(0.0, 0.002), 0.8)

# Provides a time-of-use electricity price signal.
class PriceModel:
    def __init__(self, slots_per_day: int = 96):
        self.slots_per_day = slots_per_day

    def get_price(self, slot: int) -> float:
        s = (slot % self.slots_per_day) / self.slots_per_day
        base = 0.18
        peak = 0.22 * math.exp(-((s - 0.82)**2) / 0.0025)
        midday = -0.07 * math.exp(-((s - 0.50)**2) / 0.0040)
        return max(0.03, base + peak + midday + random.uniform(-0.01, 0.01))

# Base class for battery dispatch strategies.
class BatteryPolicy:
    name = "base"
    def decide_power_kw(self, **kwargs):
        return 0.0

# A battery policy that prioritizes storing surplus PV and meeting local load.
class SelfConsumptionPolicy(BatteryPolicy):
    name = "self_consumption"
    def decide_power_kw(self, *, load_kw, pv_kw, soc_kwh, capacity_kwh, power_limit_kw, slot_duration_h, **kwargs):
        net = load_kw - pv_kw
        if net > 0:
            return min(net, power_limit_kw, (soc_kwh / slot_duration_h) * 0.92)
        else:
            return -min(-net, power_limit_kw, ((capacity_kwh - soc_kwh) / slot_duration_h) / 0.92)

# A battery policy that charges at low prices and discharges at high prices.
class ProfitMaxPolicy(BatteryPolicy):
    name = "profit_max"
    def __init__(self, price_low=0.12, price_high=0.28):
        self.price_low = price_low
        self.price_high = price_high

    def decide_power_kw(self, *, price, soc_kwh, capacity_kwh, power_limit_kw, slot_duration_h, **kwargs):
        if price <= self.price_low:
            return -min(power_limit_kw, ((capacity_kwh - soc_kwh) / slot_duration_h) / 0.92)
        if price >= self.price_high:
            return min(power_limit_kw, (soc_kwh / slot_duration_h) * 0.92)
        return SelfConsumptionPolicy().decide_power_kw(soc_kwh=soc_kwh, capacity_kwh=capacity_kwh, power_limit_kw=power_limit_kw, slot_duration_h=slot_duration_h, **kwargs)