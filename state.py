import json
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path

@dataclass
class Candidate:
    full_name: str
    url: str
    description: str
    stars: int
    language: str|None
    matched_clusters: list[str]

    is_accepted: bool | None = None
    validation_reason: str | None = None
    explanation_eg: str | None = None
    explanation_ka: str | None = None 


