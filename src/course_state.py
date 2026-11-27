"""学院课程状态机服务 CourseStateService（ClawHunt #1884 样品）。

任务编码 S6-ACAD-010：所有课程状态变化只能经本服务完成。
状态：draft / pending_review / published / offline / archived。

合法流转（6 条）：
  draft -> pending_review   提交审核（运营专员；校验必填）
  pending_review -> published  审批通过（运营主管；提交人不能审自己的课）
  pending_review -> draft   驳回（必须填写驳回原因）
  published -> offline     下架（有报名中课期时须 confirm=true，返回受影响报名数）
  offline -> pending_review  重新上架（需再次审批）
  offline -> archived      归档（归档后只读）

规则：
  - published 状态下只允许改 sort_weight 和课期，其他对外内容须先下架再改。
  - 非法流转抛 InvalidTransitionError，错误码 ACAD_COURSE_INVALID_TRANSITION（接口层映射为 409）。
  - 每次流转写一条审计日志并发出领域事件 academy.course.status_changed。

依赖 S2-IAM（身份、角色）与 S2-AUDIT（审计日志）此处用接口桩/内存实现，
见 stubs.py；真实对接点写在 README。
只用虚构测试数据，不含任何真实个人信息或企业信息。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

ERROR_INVALID_TRANSITION = "ACAD_COURSE_INVALID_TRANSITION"
ERROR_SELF_APPROVAL = "ACAD_COURSE_SELF_APPROVAL"
ERROR_CONFIRM_REQUIRED = "ACAD_COURSE_CONFIRM_REQUIRED"
ERROR_REASON_REQUIRED = "ACAD_COURSE_REASON_REQUIRED"
ERROR_PUBLISHED_FIELD_LOCKED = "ACAD_COURSE_PUBLISHED_FIELD_LOCKED"
ERROR_ARCHIVED_READONLY = "ACAD_COURSE_ARCHIVED_READONLY"

EVENT_STATUS_CHANGED = "academy.course.status_changed"

STATES = {"draft", "pending_review", "published", "offline", "archived"}

ALLOWED_TRANSITIONS = {
    ("draft", "pending_review"),
    ("pending_review", "published"),
    ("pending_review", "draft"),
    ("published", "offline"),
    ("offline", "pending_review"),
    ("offline", "archived"),
}

# published 状态下允许修改的字段；其余对外内容须先下架再改。
PUBLISHED_EDITABLE_FIELDS = {"sort_weight", "sessions"}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class InvalidTransitionError(Exception):
    """非法流转。error_code 固定为 ACAD_COURSE_INVALID_TRANSITION。"""

    def __init__(self, from_status: str, to_status: str):
        super().__init__(f"invalid transition {from_status} -> {to_status}")
        self.error_code = ERROR_INVALID_TRANSITION
        self.from_status = from_status
        self.to_status = to_status


class SelfApprovalError(Exception):
    """提交人审批自己提交的课程（403）。"""

    def __init__(self):
        super().__init__("submitter cannot approve their own course")
        self.error_code = ERROR_SELF_APPROVAL


class MissingFieldsError(Exception):
    """提交审核时必填缺失，missing 为缺失项列表。"""

    def __init__(self, missing: list[str]):
        super().__init__(f"missing required fields: {', '.join(missing)}")
        self.error_code = "ACAD_COURSE_MISSING_FIELDS"
        self.missing = missing


class ConfirmRequiredError(Exception):
    """下架时存在报名中课期但未传 confirm=true。"""

    def __init__(self, affected_enrollments: int):
        super().__init__("confirm=true required when active enrollments exist")
        self.error_code = ERROR_CONFIRM_REQUIRED
        self.affected_enrollments = affected_enrollments


class ReasonRequiredError(Exception):
    """驳回时未填驳回原因。"""

    def __init__(self):
        super().__init__("reject reason is required")
        self.error_code = ERROR_REASON_REQUIRED


class PublishedFieldLockedError(Exception):
    """published 状态下修改了受锁定的对外内容字段。"""

    def __init__(self, fields: list[str]):
        super().__init__(f"fields locked while published: {', '.join(fields)}")
        self.error_code = ERROR_PUBLISHED_FIELD_LOCKED
        self.fields = fields


class ArchivedReadOnlyError(Exception):
    """归档后只读，任何修改都被拒绝。"""

    def __init__(self):
        super().__init__("archived course is read-only")
        self.error_code = ERROR_ARCHIVED_READONLY


@dataclass
class Course:
    """课程聚合根（样品用内存模型；生产由 SQLAlchemy 实体替换）。"""

    id: str
    title: str = ""
    category: str = ""
    intro: str = ""
    lecturers: list[str] = field(default_factory=list)
    sessions: list[str] = field(default_factory=list)
    status: str = "draft"
    sort_weight: int = 0
    submitted_by: str | None = None
    active_enrollments: int = 0


@dataclass
class Actor:
    user_id: str
    role: str  # "operator"（运营专员）/ "supervisor"（运营主管）


class CourseStateService:
    """课程状态流转服务：唯一允许改变课程状态的入口。"""

    def __init__(self, audit_log: list[dict] | None = None,
                 event_sink: list[dict] | None = None):
        self.audit_log: list[dict] = audit_log if audit_log is not None else []
        self.event_sink: list[dict] = event_sink if event_sink is not None else []

    # -- 内部：统一落流转 -------------------------------------------------
    def _apply(self, course: Course, actor: Actor, to_status: str,
               reason: str = "") -> dict:
        from_status = course.status
        if (from_status, to_status) not in ALLOWED_TRANSITIONS:
            raise InvalidTransitionError(from_status, to_status)
        course.status = to_status
        entry = {
            "course_id": course.id,
            "actor": actor.user_id,
            "from_status": from_status,
            "to_status": to_status,
            "reason": reason,
            "at": _now_iso(),
        }
        self.audit_log.append(entry)
        self.event_sink.append({
            "type": EVENT_STATUS_CHANGED,
            "course_id": course.id,
            "from_status": from_status,
            "to_status": to_status,
            "actor": actor.user_id,
            "at": entry["at"],
        })
        return {"course_id": course.id, "from_status": from_status,
                "to_status": to_status}

    # -- 6 条合法流转 ------------------------------------------------------
    def submit(self, course: Course, actor: Actor) -> dict:
        """draft -> pending_review。校验必填：标题、类别、简介、>=1 主讲、>=1 课期。"""
        missing: list[str] = []
        if not course.title:
            missing.append("title")
        if not course.category:
            missing.append("category")
        if not course.intro:
            missing.append("intro")
        if not course.lecturers:
            missing.append("lecturers")
        if not course.sessions:
            missing.append("sessions")
        if missing:
            raise MissingFieldsError(missing)
        course.submitted_by = actor.user_id
        return self._apply(course, actor, "pending_review")

    def approve(self, course: Course, actor: Actor) -> dict:
        """pending_review -> published。运营主管审批；提交人不能审自己的课。"""
        # 先判流转合法性：归档等终端态上的 approve 是非法流转（409），
        # 不是自审问题（403）；自审拦截只对合法的待审批流转生效。
        if (course.status, "published") not in ALLOWED_TRANSITIONS:
            raise InvalidTransitionError(course.status, "published")
        if course.submitted_by is not None and course.submitted_by == actor.user_id:
            raise SelfApprovalError()
        return self._apply(course, actor, "published")

    def reject(self, course: Course, actor: Actor, reason: str = "") -> dict:
        """pending_review -> draft。驳回必须填写驳回原因。"""
        if not reason.strip():
            raise ReasonRequiredError()
        return self._apply(course, actor, "draft", reason=reason)

    def offline(self, course: Course, actor: Actor, reason: str = "",
                confirm: bool = False) -> dict:
        """published -> offline。有报名中课期时须 confirm=true，返回受影响报名数。"""
        if course.active_enrollments > 0 and not confirm:
            raise ConfirmRequiredError(course.active_enrollments)
        result = self._apply(course, actor, "offline", reason=reason)
        result["affected_enrollments"] = course.active_enrollments
        return result

    def resubmit(self, course: Course, actor: Actor) -> dict:
        """offline -> pending_review。重新上架需再次审批。"""
        course.submitted_by = actor.user_id
        return self._apply(course, actor, "pending_review")

    def archive(self, course: Course, actor: Actor) -> dict:
        """offline -> archived。归档后只读。"""
        return self._apply(course, actor, "archived")

    # -- published 编辑守卫 -------------------------------------------------
    def update_course(self, course: Course, actor: Actor,
                      changes: dict) -> dict:
        """修改课程字段。archived 只读；published 只允许 sort_weight 与 sessions。"""
        if course.status == "archived":
            raise ArchivedReadOnlyError()
        if course.status == "published":
            locked = sorted(set(changes) - PUBLISHED_EDITABLE_FIELDS)
            if locked:
                raise PublishedFieldLockedError(locked)
        for key, value in changes.items():
            setattr(course, key, value)
        return {"course_id": course.id, "status": course.status,
                "updated_fields": sorted(changes)}
