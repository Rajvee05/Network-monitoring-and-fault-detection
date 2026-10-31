"""Deterministic, explicitly simulated topology measurements and event timeline."""
from dataclasses import dataclass, field
from datetime import datetime, timezone
from collections.abc import Mapping
from ml.predict import InferenceInputError, predict_network_fault

SCENARIOS = {"normal": "Normal network operation", "link_down": "Link down · S1—R1", "server_unreachable": "Server unreachable", "degraded": "Increased latency / packet loss"}

def diagnose_simulated_measurement(measurement, latency_limit_ms=100.0, loss_limit_pct=20.0):
    """Apply a transparent rule to synthetic measurements; never invokes ML."""
    if not measurement.get("link_available", True):
        return {"condition": "fault", "rule": "S1—R1 link unavailable", "evidence": "Simulated link state is down."}
    if not measurement.get("server_reachable", True):
        return {"condition": "fault", "rule": "Server reachability check failed", "evidence": "Simulated server connectivity check failed."}
    if measurement.get("probes_sent", 0) <= 0 or measurement.get("probes_ok", 0) <= 0:
        return {"condition": "unknown", "rule": "No successful connectivity probes", "evidence": "Cannot report healthy without at least one successful probe."}
    if measurement["latency_ms"] > latency_limit_ms or measurement["packet_loss_pct"] > loss_limit_pct:
        return {"condition": "degraded", "rule": "Latency or loss threshold exceeded", "evidence": f"Latency {measurement['latency_ms']:.1f} ms (limit {latency_limit_ms:g} ms); loss {measurement['packet_loss_pct']:.1f}% (limit {loss_limit_pct:g}%)."}
    return {"condition": "normal", "rule": "Connectivity and quality checks passed", "evidence": "Simulated probes succeeded within latency and packet-loss thresholds."}

def infer_sofi_observation(observation, artifact=None):
    """Gate model inference on provenance and an explicit SOFI feature vector.

    No topology/synthetic metric aliases are translated to SOFI feature names.
    This adapter only accepts recorded SOFI rows. No verified live-SOFI
    collection/adapter exists in this application yet.
    """
    if not isinstance(observation, Mapping):
        return {"status": "not_supported", "model_executed": False, "reason": "Expected a provenance-labeled monitoring observation."}
    source = observation.get("source")
    if source == "simulated topology":
        return {"status": "not_supported", "model_executed": False, "reason": "Generated topology counters/latency are simulation outputs, not SOFI ICMP/SNMP measurements; Random Forest skipped."}
    if source != "recorded_sofi":
        return {"status": "not_supported", "model_executed": False, "reason": "Only actual recorded SOFI rows are currently approved for this inference adapter; live source verification is not implemented."}
    features = observation.get("sofi_features")
    if not isinstance(features, Mapping):
        return {"status": "not_supported", "model_executed": False, "reason": "A named 33-feature SOFI measurement vector is required; topology observations do not contain it."}
    try:
        result = predict_network_fault(features, artifact=artifact)
    except InferenceInputError as exc:
        return {"status": "not_supported", "model_executed": False, "reason": str(exc)}
    return {"status": "ok", "model_executed": True, "reason": "Compatible SOFI feature vector validated and passed through saved inference pipeline.", "result": result}


@dataclass
class TopologySimulation:
    """Small deterministic state machine for reproducible classroom scenarios."""
    scenario: str = "normal"
    running: bool = False
    model_artifact: dict | None = field(default=None, repr=False, compare=False)
    tick_number: int = 0
    checks: int = 0
    successes: int = 0
    failures: int = 0
    total_packets: int = 0
    recovery_pending: bool = False
    active_fault: bool = False
    events: list = field(default_factory=list)
    last_measurement: dict | None = None
    last_diagnosis: dict | None = None
    last_model_assessment: dict | None = None

    def _record(self, kind, detail, wall_time=None):
        now = wall_time or datetime.now(timezone.utc).astimezone().isoformat(timespec="milliseconds")
        self.events.append({"wall_time": now, "simulation_time": f"T+{self.tick_number:02d}s", "event": kind, "detail": detail})
        self.events[:] = self.events[-300:]

    def _observation(self):
        if self.scenario == "link_down":
            return {"probes_sent": 4, "probes_ok": 0, "probes_failed": 4, "latency_ms": None, "packet_loss_pct": 100.0, "link_available": False, "server_reachable": False}
        if self.scenario == "server_unreachable":
            return {"probes_sent": 4, "probes_ok": 0, "probes_failed": 4, "latency_ms": None, "packet_loss_pct": 100.0, "link_available": True, "server_reachable": False}
        if self.scenario == "degraded":
            return {"probes_sent": 4, "probes_ok": 3, "probes_failed": 1, "latency_ms": 184.0, "packet_loss_pct": 25.0, "link_available": True, "server_reachable": True}
        return {"probes_sent": 4, "probes_ok": 4, "probes_failed": 0, "latency_ms": 8.4, "packet_loss_pct": 0.0, "link_available": True, "server_reachable": True}

    def check(self, wall_time=None):
        self.tick_number += 1
        self.checks += 1
        self._record("Monitoring measurement collected", "Simulated ICMP-style check · 4 probes · no packets sent on a real network", wall_time)
        measurement = self._observation()
        self.last_measurement = {**measurement, "source": "simulated topology", "collected_at": wall_time or datetime.now(timezone.utc).astimezone().isoformat(timespec="milliseconds")}
        self.total_packets += measurement["probes_sent"]
        self.successes += measurement["probes_ok"]
        self.failures += measurement["probes_failed"]
        self.last_model_assessment = infer_sofi_observation(self.last_measurement, artifact=self.model_artifact)
        gate_detail = self.last_model_assessment["reason"]
        self._record("Detection pipeline executed", f"SOFI compatibility gate: {gate_detail} Rule detector runs separately.", wall_time)
        diagnosis = diagnose_simulated_measurement(measurement)
        self.last_diagnosis = diagnosis
        self.active_fault = diagnosis["condition"] != "normal" or self.recovery_pending
        self._record("Fault result reported", f"{diagnosis['condition'].upper()} · {diagnosis['rule']} · {diagnosis['evidence']}", wall_time)
        if self.recovery_pending and diagnosis["condition"] == "normal" and measurement["probes_ok"] > 0:
            self._record("Recovery detected", f"Successful subsequent check: {measurement['probes_ok']}/{measurement['probes_sent']} probes; latency {measurement['latency_ms']:.1f} ms.", wall_time)
            self.recovery_pending = False
            self.active_fault = False
        return self.last_measurement, diagnosis

    def start(self, wall_time=None):
        if self.running: return
        self.running = True
        self._record("Simulation started", "Synthetic virtual topology · accelerated simulation clock · no real telemetry", wall_time)
        if self.checks == 0:
            self.scenario = "normal"
            self._record("Network operating normally", "Initial simulated baseline", wall_time)
            self.check(wall_time)

    def pause(self): self.running = False

    def inject(self, scenario, wall_time=None):
        if scenario not in SCENARIOS: raise ValueError(f"Unknown simulation scenario: {scenario}")
        was_running = self.running
        self.scenario = scenario
        self.recovery_pending = False
        self.active_fault = scenario != "normal"
        self._record("Network operating normally" if scenario == "normal" else "Simulated fault injected", SCENARIOS[scenario], wall_time)
        return self.check(wall_time) if was_running else None

    def clear_fault(self, wall_time=None):
        was_fault_active = self.active_fault
        self.scenario = "normal"
        self.recovery_pending = was_fault_active
        event = "Simulated fault cleared" if was_fault_active else "Recovery check requested"
        detail = "Awaiting a subsequent successful connectivity check; health is not assumed." if was_fault_active else "No active fault; only a measured successful check can report healthy."
        self._record(event, detail, wall_time)
        return self.check(wall_time) if self.running else None

    def reset(self):
        self.scenario = "normal"
        self.running = False
        self.tick_number = self.checks = self.successes = self.failures = self.total_packets = 0
        self.recovery_pending = self.active_fault = False
        self.events.clear()
        self.last_measurement = self.last_diagnosis = self.last_model_assessment = None
