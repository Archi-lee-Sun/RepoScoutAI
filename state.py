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
    explanation_en: str | None = None
    explanation_ka: str | None = None 


class PollerState:
    def __init__(self , file_path: str = "state.json"):
        self.file_path = Path(file_path)
        self._data = self._load()

    def _load(self) -> dict :
        if not self.file_path.exists():
            return {}
        with open(self.file_path , "r" , encoding="utf-8") as f :
            return json.load(f)

    def _save(self) :
        with open(self.file_path , "w" , encoding="utf-8") as f :
            json.dump(self._data , f , indent=2)

    def get_last_checked(self , cluster_name: str) -> str | None :
        return self._data.get(cluster_name)

    def update_last_checked(self , cluster_name , timestamp: str) :
        self._data[cluster_name] = timestamp
        self._save()


class PreferenceMemory :
    def __init__(self ,  file_path: str = "history.json") :
        self.file_path = Path(file_path)
        self._data = self._load()

    def _load(self) -> dict :
        if not self.file_path.exists():
            return []
        with open(self.file_path , "r" , encoding="utf-8") as f :
            return json.load(f)

    def _save(self) :
        with open(self.file_path , "w" , encoding="utf-8") as f :
            json.dump(self._data , f , indent=2)

    def add_decision(self , full_name: str , url: str , decision: str , description: str , reason: str) :
        record = {
            "full_name" : full_name ,
            "url" : url , 
            "decision" : decision ,
            "description" : description , 
            "reason" : reason
        }

        self._data.append(record)
        self._save()

        

