from pydantic import BaseModel
from typing import TypeVar, Generic, Optional

T = TypeVar("T")


class Resp(BaseModel, Generic[T]):
    code: int = 0
    msg: str = "ok"
    data: Optional[T] = None

    @classmethod
    def ok(cls, data: T = None, msg: str = "ok"):
        return cls(code=0, msg=msg, data=data)

    @classmethod
    def fail(cls, msg: str, code: int = 1):
        return cls(code=code, msg=msg, data=None)
