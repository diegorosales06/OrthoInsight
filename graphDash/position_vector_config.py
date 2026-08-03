import threading
import yaml
from pathlib import Path


class PositionVectorConfig:
    """Thread-safe position vector configuration manager."""

    DEFAULT_VECTOR = {"x": 1.0, "y": 1.0, "z": 1.0}

    def __init__(self, config_path=None):
        """
        Args:
            config_path: path to position_vector.yaml (defaults to config/position_vector.yaml)
        """
        if config_path is None:
            config_path = Path(__file__).parent.parent / "config" / "position_vector.yaml"
        else:
            config_path = Path(config_path)

        self.config_path = config_path
        self.lock = threading.Lock()
        self.vector = self.DEFAULT_VECTOR.copy()
        self._load()

    def _load(self):
        """Load position vector from YAML file. Creates default if missing."""
        if self.config_path.exists():
            try:
                with open(self.config_path, "r") as f:
                    data = yaml.safe_load(f)
                    if data and "position_vector" in data:
                        pv = data["position_vector"]
                        self.vector = {
                            "x": float(pv.get("x", self.DEFAULT_VECTOR["x"])),
                            "y": float(pv.get("y", self.DEFAULT_VECTOR["y"])),
                            "z": float(pv.get("z", self.DEFAULT_VECTOR["z"])),
                        }
            except Exception as e:
                print(f"Error loading position_vector.yaml: {e}. Using defaults.")
                self.vector = self.DEFAULT_VECTOR.copy()
        else:
            self._save()

    def _save(self):
        """Save position vector to YAML file."""
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with open(self.config_path, "w") as f:
                yaml.dump({"position_vector": self.vector}, f)
        except Exception as e:
            print(f"Error saving position_vector.yaml: {e}")

    def get_vector(self):
        """Returns (x, y, z) tuple."""
        with self.lock:
            return self.vector["x"], self.vector["y"], self.vector["z"]

    def set_vector(self, x, y, z):
        """Set position vector components."""
        with self.lock:
            self.vector = {
                "x": float(x),
                "y": float(y),
                "z": float(z),
            }

    def save(self):
        """Persist to YAML file."""
        with self.lock:
            self._save()

    def reset_to_default(self):
        """Reset to default values (1.0, 1.0, 1.0)."""
        self.set_vector(*self.DEFAULT_VECTOR.values())
        self.save()
