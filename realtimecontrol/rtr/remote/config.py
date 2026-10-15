import json
import sys
from dataclasses import dataclass
from pathlib import Path
from argparse import Namespace

@dataclass
class Config:
    core_host: str = "localhost"
    core_port: int = 5000
    device: str = "/dev/input/js0"
    height: int = 24
    colors: dict = None
    font: str = "default"
    http_poll_interval: float = 1.0

    def __post_init__(self):
        if self.colors is None:
            self.colors = {
                "base": "#000000",
                "header": "#6963ff",
                "header_text": "#ffffff",
                "body": "#ffffff",
                "body_text": "#000000",
                "selected": "#6963ff",
                "selected_text": "#ffffff",
                "focus": "#6963ff",
                "focus_text": "#ffffff",
                "button": "#006600",
                "button_text": "#ffffff",
                "error": "#ff0000",
                "error_text": "#ffffff",
            }

def from_args(args: Namespace) -> Config:
    base = Path(__file__).resolve().parent

    config_file = args.config or (base / "config.json")
    if not config_file.is_file():
        raise FileNotFoundError(f"Config not found: {config_file}")

    with config_file.open("r") as f:
        cfg = json.load(f)

    return Config(**cfg)
