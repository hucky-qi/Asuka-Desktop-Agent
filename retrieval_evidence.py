"""Internal immutable retrieval evidence; never supplied by the model or UI."""
from dataclasses import dataclass
from pathlib import Path
from identifier_intent import satisfied

@dataclass(frozen=True)
class SemanticEvidence:
    path: str
    identifiers: tuple[str, ...]

    def matches(self, path, identifiers, groups=None):
        return self.path == str(Path(path).resolve()) and satisfied(self.identifiers, groups if groups is not None else (tuple(identifiers),))
