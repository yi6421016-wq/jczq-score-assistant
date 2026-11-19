from dataclasses import dataclass, field


@dataclass
class ModelOutput:
    name: str
    available: bool
    probs: tuple | None = None          # (主胜, 平, 客胜)
    matrix: dict | None = None          # {(i,j): p}
    note: str = ""
    meta: dict = field(default_factory=dict)

    @property
    def pick(self):
        if not self.probs:
            return "—"
        return ("主胜", "平局", "客胜")[max(range(3), key=lambda i: self.probs[i])]

    def to_dict(self):
        return {
            "name": self.name, "available": self.available,
            "probs": [round(x, 4) for x in self.probs] if self.probs else None,
            "pick": self.pick, "note": self.note, "meta": self.meta,
        }


def unavailable(name, note):
    return ModelOutput(name=name, available=False, note=note)