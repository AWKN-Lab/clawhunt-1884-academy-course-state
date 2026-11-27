"""流转接口适配层（ClawHunt #1884 样品）。

对应任务约定的接口：
  POST /api/admin/academy/courses/{id}/transitions
  请求体 {action, reason, confirm}

本样品不用 Web 框架依赖：handle_transition() 是该接口的纯函数等价实现，
返回 (http_status, body)，状态码与错误码映射与 FastAPI 实现一致；
生产接线时把本函数体原样挂到 FastAPI 路由上（见 README 的接线示例）。
"""

from __future__ import annotations

from .course_state import (
    ERROR_INVALID_TRANSITION,
    Actor,
    ConfirmRequiredError,
    Course,
    CourseStateService,
    InvalidTransitionError,
    MissingFieldsError,
    PublishedFieldLockedError,
    ArchivedReadOnlyError,
    ReasonRequiredError,
    SelfApprovalError,
)

ACTIONS = {
    "submit": "pending_review",
    "approve": "published",
    "reject": "draft",
    "offline": "offline",
    "resubmit": "pending_review",
    "archive": "archived",
}


def handle_transition(service: CourseStateService, store: dict[str, Course],
                      course_id: str, actor: Actor, action: str,
                      reason: str = "", confirm: bool = False):
    """处理一次流转请求，返回 (http_status, body)。"""
    course = store.get(course_id)
    if course is None:
        return 404, {"error_code": "ACAD_COURSE_NOT_FOUND",
                     "course_id": course_id}
    if action not in ACTIONS:
        return 400, {"error_code": "ACAD_COURSE_UNKNOWN_ACTION",
                     "action": action}
    try:
        if action == "submit":
            result = service.submit(course, actor)
        elif action == "approve":
            result = service.approve(course, actor)
        elif action == "reject":
            result = service.reject(course, actor, reason=reason)
        elif action == "offline":
            result = service.offline(course, actor, reason=reason,
                                     confirm=confirm)
        elif action == "resubmit":
            result = service.resubmit(course, actor)
        else:  # archive
            result = service.archive(course, actor)
    except InvalidTransitionError as exc:
        return 409, {"error_code": ERROR_INVALID_TRANSITION,
                     "from_status": exc.from_status,
                     "to_status": exc.to_status}
    except SelfApprovalError as exc:
        return 403, {"error_code": exc.error_code}
    except MissingFieldsError as exc:
        return 422, {"error_code": exc.error_code, "missing": exc.missing}
    except ConfirmRequiredError as exc:
        return 422, {"error_code": exc.error_code,
                     "affected_enrollments": exc.affected_enrollments}
    except ReasonRequiredError as exc:
        return 422, {"error_code": exc.error_code}
    return 200, result


def handle_update(service: CourseStateService, store: dict[str, Course],
                  course_id: str, actor: Actor, changes: dict):
    """处理一次课程字段修改请求，返回 (http_status, body)。"""
    course = store.get(course_id)
    if course is None:
        return 404, {"error_code": "ACAD_COURSE_NOT_FOUND",
                     "course_id": course_id}
    try:
        result = service.update_course(course, actor, changes)
    except (PublishedFieldLockedError, ArchivedReadOnlyError) as exc:
        return 409, {"error_code": exc.error_code,
                     "fields": getattr(exc, "fields", [])}
    return 200, result
