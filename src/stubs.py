"""依赖接口桩（ClawHunt #1884 样品）。

本单依赖 S2-IAM（身份、角色与数据范围）与 S2-AUDIT（审计日志），
样品先用内存桩跑通，真实对接点如下，生产实现时替换本文件同名类即可，
CourseStateService 与 api.py 不用改：

- S2-IAM：IamClient.get_actor(user_id) -> Actor（user_id、role）。
  角色取值为 operator（运营专员，可提交/驳回/下架/归档）与
  supervisor（运营主管，可审批通过）；审批接口须校验 role == supervisor，
  本样品的 handle_transition() 调用方在 FastAPI 依赖里做该校验。
- S2-AUDIT：AuditSink.append(entry)；CourseStateService 每次流转产生的
  entry（操作人、前后状态、原因、时间）经此接口写入审计日志表。
  样品直接用内存 list 充当 sink，测试断言同一条目结构。
- 领域事件：EventBus.publish({"type": "academy.course.status_changed", ...})；
  样品用内存 list 充当 bus，测试断言事件类型与前后状态。
"""

from __future__ import annotations

from .course_state import Actor


class IamStub:
    """S2-IAM 内存桩：user_id -> role。"""

    def __init__(self, roles: dict[str, str] | None = None):
        self.roles = roles or {}

    def get_actor(self, user_id: str) -> Actor:
        return Actor(user_id=user_id, role=self.roles.get(user_id, "operator"))


class AuditStub:
    """S2-AUDIT 内存桩：append 即记入内存列表。"""

    def __init__(self):
        self.entries: list[dict] = []

    def append(self, entry: dict) -> None:
        self.entries.append(entry)


class EventBusStub:
    """领域事件内存桩。"""

    def __init__(self):
        self.events: list[dict] = []

    def publish(self, event: dict) -> None:
        self.events.append(event)
