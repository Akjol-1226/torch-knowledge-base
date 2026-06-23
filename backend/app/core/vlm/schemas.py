from pydantic import BaseModel


class NotsureSpan(BaseModel):
    start: int
    end: int
    text: str


class VLMResponse(BaseModel):
    raw_text: str
    notsure_segments: list[NotsureSpan]
    model_id: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    latency_ms: int = 0
