from pydantic import BaseModel, Field

VISA_ASSESSMENT_SCHEMA = {
    "type": "object",
    "properties": {
        "candidate_visa_types": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "visa_type": {"type": "string"},
                    "likelihood": {"type": "number", "minimum": 0, "maximum": 1},
                    "reasoning": {"type": "string"},
                },
                "required": ["visa_type", "likelihood", "reasoning"],
            },
        },
        "confidence": {
            "type": "number",
            "minimum": 0,
            "maximum": 1,
            "description": "Overall confidence in the top candidate visa type, grounded ONLY in the provided knowledge base excerpts.",
        },
        "missing_info": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Facts still needed from the user before a confident determination can be made.",
        },
        "citations": {
            "type": "array",
            "items": {"type": "string"},
            "description": "citation_id values from the provided knowledge base excerpts that support this assessment. Never invent a citation_id.",
        },
        "contradictions": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Any facts the user has stated that conflict with earlier statements in this conversation.",
        },
        "high_stakes_flags": {
            "type": "array",
            "items": {
                "type": "string",
                "enum": ["prior_refusal", "criminal_record", "asylum_related", "other_high_stakes"],
            },
            "description": "Flag ANY mention of these, however indirect. These always route to human review regardless of confidence.",
        },
        "ready_for_determination": {
            "type": "boolean",
            "description": "True only if you have enough information to state a confident, well-cited visa type determination.",
        },
        "next_question": {
            "type": ["string", "null"],
            "description": "If not ready_for_determination, the single next question to ask the user. Null if ready.",
        },
        "reply_to_user": {
            "type": "string",
            "description": "The natural-language message to send back to the user this turn (the question, or a summary if ready).",
        },
    },
    "required": [
        "candidate_visa_types",
        "confidence",
        "missing_info",
        "citations",
        "contradictions",
        "high_stakes_flags",
        "ready_for_determination",
        "next_question",
        "reply_to_user",
    ],
}


class CandidateVisaType(BaseModel):
    visa_type: str
    likelihood: float
    reasoning: str


class VisaAssessment(BaseModel):
    candidate_visa_types: list[CandidateVisaType] = Field(default_factory=list)
    confidence: float = 0.0
    missing_info: list[str] = Field(default_factory=list)
    citations: list[str] = Field(default_factory=list)
    contradictions: list[str] = Field(default_factory=list)
    high_stakes_flags: list[str] = Field(default_factory=list)
    ready_for_determination: bool = False
    next_question: str | None = None
    reply_to_user: str = ""

    @property
    def top_candidate(self) -> CandidateVisaType | None:
        if not self.candidate_visa_types:
            return None
        return max(self.candidate_visa_types, key=lambda c: c.likelihood)
