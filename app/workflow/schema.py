import json
from dataclasses import dataclass, field
from typing import Any

from app.config import SCHEMAS_DIR

_SCHEMA_FILES = {
    "Standard Visitor": "standard_visitor.json",
    "Skilled Worker": "skilled_worker.json",
    "Student": "student.json",
    "Family (Partner)": "family_partner.json",
}


@dataclass(frozen=True)
class Condition:
    field: str
    op: str  # "eq" | "in"
    value: Any

    def evaluate(self, context: dict[str, str]) -> bool:
        actual = context.get(self.field)
        if actual is None:
            return False
        if self.op == "eq":
            return actual == self.value
        if self.op == "in":
            return actual in self.value
        raise ValueError(f"Unknown condition operator: {self.op}")


@dataclass(frozen=True)
class Requirement:
    name: str
    kind: str  # "field" | "document"
    validator: str
    prompt: str
    validator_args: dict[str, Any] = field(default_factory=dict)
    condition: Condition | None = None

    def applies(self, context: dict[str, str]) -> bool:
        return self.condition is None or self.condition.evaluate(context)


@dataclass(frozen=True)
class VisaSchema:
    visa_type: str
    requirements: list[Requirement]


def load_schema(visa_type: str) -> VisaSchema:
    filename = _SCHEMA_FILES.get(visa_type)
    if filename is None:
        raise ValueError(f"No requirement schema defined for visa type: {visa_type}")
    raw = json.loads((SCHEMAS_DIR / filename).read_text(encoding="utf-8"))
    requirements = []
    for r in raw["requirements"]:
        condition = None
        if "condition" in r:
            condition = Condition(field=r["condition"]["field"], op=r["condition"]["op"], value=r["condition"]["value"])
        requirements.append(
            Requirement(
                name=r["name"],
                kind=r["kind"],
                validator=r["validator"],
                prompt=r["prompt"],
                validator_args=r.get("validator_args", {}),
                condition=condition,
            )
        )
    return VisaSchema(visa_type=raw["visa_type"], requirements=requirements)
