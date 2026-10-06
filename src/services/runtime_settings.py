"""AgentCore Platform v1.0"""

# Runtime settings for SVC-C2-007.
#
# config/config.yaml holds the runtime parameters. The registry loads that file
# and passes it to the graph constructor; the standalone entry point reads it
# through runtime_config() so both deployments run on the same declaration.
#
# Every declared value is validated once here, against explicit bounds, and then
# travels to its consumer through inner-graph state. A value that is absent,
# mistyped, non-finite or out of range falls back to the built-in floor rather
# than reaching a consumer that could not use it — there is no second copy of
# the defaults anywhere else on disk.

import json
import math
from pathlib import Path
from typing import Any, Dict, Optional, cast

# src/services/runtime_settings.py -> parents[2] is the repository root.
RUNTIME_CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "config.yaml"

# Floor used only for keys config/config.yaml does not declare, or declares out
# of contract. A value present in the file always wins, which is what makes the
# declaration observable end to end.
BUILTIN_SETTINGS: Dict[str, Any] = {
    "target_utilization": 0.85,
    "pipeline_health_threshold": 2.0,
    "max_visualizations": 3,
}

# Bounds every declared setting is checked against.
TARGET_UTILIZATION_MIN, TARGET_UTILIZATION_MAX = 0.0, 1.0
PIPELINE_THRESHOLD_MIN, PIPELINE_THRESHOLD_MAX = 0.0, 100.0
MAX_VISUALIZATIONS_MIN, MAX_VISUALIZATIONS_MAX = 1, 10


def finite_in_range(value: Any, lo: float, hi: float) -> Optional[float]:
    """Parse one number: real, finite, inside [lo, hi]. Returns None otherwise.

    Booleans are rejected explicitly, because bool is a subclass of int and
    ``True`` would otherwise be accepted as 1. Strings are parsed, so "NaN" and
    "Infinity" — which float() accepts and which arrive verbatim through JSON —
    are rejected here rather than silently comparing False against every later
    bound and letting the value through unchecked.
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, str):
        try:
            parsed = float(value.strip())
        except (TypeError, ValueError):
            return None
    elif isinstance(value, (int, float)):
        parsed = float(value)
    else:
        return None
    if not math.isfinite(parsed):
        return None
    if not lo <= parsed <= hi:
        return None
    return parsed


def runtime_config() -> Dict[str, Any]:
    """Read the runtime parameters from config/config.yaml.

    Returns an empty mapping — never raises — when the file is absent,
    unreadable, not valid YAML or not a mapping; the graph then runs on the
    built-in floor. PyYAML is imported lazily because it is a framework runtime
    dependency rather than a module-load coupling of this template.
    """
    try:
        import yaml

        loaded = yaml.safe_load(RUNTIME_CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}
    if not isinstance(loaded, dict):
        return {}
    return cast(Dict[str, Any], loaded)


def declared_settings(config: Dict[str, Any]) -> Dict[str, Any]:
    """Validate and flatten the declared runtime settings for the pipeline.

    `config` is what the graph was constructed with — the contents of
    config/config.yaml. Each value is checked for type, finiteness and range; an
    absent or out-of-contract key keeps the built-in floor.
    """
    settings: Dict[str, Any] = dict(BUILTIN_SETTINGS)

    analysis = config.get("analysis")
    analysis = analysis if isinstance(analysis, dict) else {}
    target = finite_in_range(analysis.get("target_utilization"), TARGET_UTILIZATION_MIN, TARGET_UTILIZATION_MAX)
    if target is not None:
        settings["target_utilization"] = target

    report = config.get("report")
    report = report if isinstance(report, dict) else {}
    threshold = finite_in_range(report.get("pipeline_health_threshold"), PIPELINE_THRESHOLD_MIN, PIPELINE_THRESHOLD_MAX)
    if threshold is not None:
        settings["pipeline_health_threshold"] = threshold

    cap = finite_in_range(report.get("max_visualizations"), MAX_VISUALIZATIONS_MIN, MAX_VISUALIZATIONS_MAX)
    if cap is not None and cap == int(cap):
        settings["max_visualizations"] = int(cap)

    return settings


def settings_from_state(state: Any) -> Dict[str, Any]:
    """Read the validated settings the graph seeded into state.

    Returns the built-in floor when the field is absent or unreadable, so a node
    invoked outside a graph still runs on a complete settings mapping.
    """
    floor: Dict[str, Any] = dict(BUILTIN_SETTINGS)
    raw = state.get("runtime_settings") if hasattr(state, "get") else None
    if not isinstance(raw, str) or not raw:
        return floor
    try:
        loaded = json.loads(raw)
    except (TypeError, ValueError):
        return floor
    if not isinstance(loaded, dict):
        return floor
    floor.update({key: loaded[key] for key in BUILTIN_SETTINGS if key in loaded})
    return floor
