from typing import Any

from pydantic import BaseModel, Field


class ScreenRequest(BaseModel):
    filters: dict[str, Any] = Field(default_factory=dict)
    sort: str = "ret_3m"
    desc: bool = True
    limit: int = 50
