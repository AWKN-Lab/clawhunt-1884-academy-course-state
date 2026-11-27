"""课程状态机测试（ClawHunt #1884 / S6-ACAD-010）。

覆盖平台验收标准 4 条：
  AC1 6 条合法流转各有测试用例通过；任一非法流转返回 409 与
      ACAD_COURSE_INVALID_TRANSITION
  AC2 提交人审批自己提交的课程时返回 403
  AC3 每次流转在审计日志中有一条含前后状态的记录
  AC4 缺少主讲或课期时提交审核失败，返回缺失项列表

全部使用虚构测试数据（课程名、讲师名均为虚构）。
"""

import pytest

from src.api import handle_transition, handle_update
from src.course_state import Actor, Course, CourseStateService

OPERATOR = Actor(user_id="user-operator-001", role="operator")
SUPERVISOR = Actor(user_id="user-supervisor-001", role="supervisor")
OTHER_SUPERVISOR = Actor(user_id="user-supervisor-002", role="supervisor")


def make_course(**overrides):
    data = {
        "id": "course-0001",
        "title": "虚构课程：会员空间运营入门",
        "category": "运营",
        "intro": "虚构简介：面向新会员的运营基础课。",
        "lecturers": ["讲师甲"],
        "sessions": ["2027-02-01 10:00"],
    }
    data.update(overrides)
    return Course(**data)


@pytest.fixture
def svc():
    return CourseStateService()


@pytest.fixture
def store():
    return {}


def transition(svc, store, course_id, actor, action, **kw):
    return handle_transition(svc, store, course_id, actor, action, **kw)


def published_course(svc, store):
    course = make_course()
    store[course.id] = course
    assert transition(svc, store, course.id, OPERATOR, "submit")[0] == 200
    assert transition(svc, store, course.id, SUPERVISOR, "approve")[0] == 200
    return course


# -- AC1：6 条合法流转 ------------------------------------------------------

def test_legal_submit_draft_to_pending_review(svc, store):
    course = make_course()
    store[course.id] = course
    status, body = transition(svc, store, course.id, OPERATOR, "submit")
    assert status == 200
    assert body == {"course_id": course.id, "from_status": "draft",
                    "to_status": "pending_review"}
    assert course.status == "pending_review"


def test_legal_approve_pending_review_to_published(svc, store):
    course = make_course()
    store[course.id] = course
    transition(svc, store, course.id, OPERATOR, "submit")
    status, body = transition(svc, store, course.id, SUPERVISOR, "approve")
    assert status == 200
    assert body["from_status"] == "pending_review"
    assert body["to_status"] == "published"


def test_legal_reject_pending_review_to_draft(svc, store):
    course = make_course()
    store[course.id] = course
    transition(svc, store, course.id, OPERATOR, "submit")
    status, body = transition(svc, store, course.id, SUPERVISOR, "reject",
                              reason="简介缺少课纲")
    assert status == 200
    assert body["to_status"] == "draft"
    assert course.status == "draft"


def test_legal_offline_published_to_offline(svc, store):
    course = published_course(svc, store)
    status, body = transition(svc, store, course.id, OPERATOR, "offline")
    assert status == 200
    assert body["to_status"] == "offline"
    assert body["affected_enrollments"] == 0


def test_legal_resubmit_offline_to_pending_review(svc, store):
    course = published_course(svc, store)
    transition(svc, store, course.id, OPERATOR, "offline")
    status, body = transition(svc, store, course.id, OPERATOR, "resubmit")
    assert status == 200
    assert body["from_status"] == "offline"
    assert body["to_status"] == "pending_review"


def test_legal_archive_offline_to_archived(svc, store):
    course = published_course(svc, store)
    transition(svc, store, course.id, OPERATOR, "offline")
    status, body = transition(svc, store, course.id, OPERATOR, "archive")
    assert status == 200
    assert body["to_status"] == "archived"


def test_resubmit_requires_approval_again(svc, store):
    course = published_course(svc, store)
    transition(svc, store, course.id, OPERATOR, "offline")
    transition(svc, store, course.id, OPERATOR, "resubmit")
    assert course.status == "pending_review"
    status, _ = transition(svc, store, course.id, SUPERVISOR, "approve")
    assert status == 200
    assert course.status == "published"


# -- AC1：非法流转返回 409 与错误码 -------------------------------------------

@pytest.mark.parametrize("from_action,action", [
    ("submit", "approve"),   # draft -> published（跳过审核）
    ("submit", "offline"),   # draft -> offline
    ("submit", "archive"),   # draft -> archived
])
def test_illegal_from_draft(svc, store, from_action, action):
    course = make_course()
    store[course.id] = course
    status, body = transition(svc, store, course.id, SUPERVISOR, action)
    assert status == 409
    assert body["error_code"] == "ACAD_COURSE_INVALID_TRANSITION"


def test_illegal_published_to_draft(svc, store):
    course = published_course(svc, store)
    status, body = transition(svc, store, course.id, SUPERVISOR, "reject",
                              reason="想直接打回")
    assert status == 409
    assert body["error_code"] == "ACAD_COURSE_INVALID_TRANSITION"


def test_illegal_archived_is_terminal(svc, store):
    course = published_course(svc, store)
    transition(svc, store, course.id, OPERATOR, "offline")
    transition(svc, store, course.id, OPERATOR, "archive")
    for action in ("submit", "approve", "offline", "resubmit", "archive"):
        status, body = transition(svc, store, course.id, OPERATOR, action)
        assert status == 409
        assert body["error_code"] == "ACAD_COURSE_INVALID_TRANSITION"


def test_unknown_course_404(svc, store):
    status, body = transition(svc, store, "course-missing", OPERATOR, "submit")
    assert status == 404


# -- AC2：提交人不能审批自己的课程 --------------------------------------------

def test_self_approval_returns_403(svc, store):
    course = make_course()
    store[course.id] = course
    transition(svc, store, course.id, OPERATOR, "submit")
    approver = Actor(user_id="user-operator-001", role="supervisor")
    status, body = transition(svc, store, course.id, approver, "approve")
    assert status == 403
    assert body["error_code"] == "ACAD_COURSE_SELF_APPROVAL"
    assert course.status == "pending_review"


def test_other_supervisor_can_approve(svc, store):
    course = make_course()
    store[course.id] = course
    transition(svc, store, course.id, OPERATOR, "submit")
    status, _ = transition(svc, store, course.id, OTHER_SUPERVISOR, "approve")
    assert status == 200


# -- AC3：审计日志与领域事件 ---------------------------------------------------

def test_audit_log_records_every_transition(svc, store):
    course = published_course(svc, store)
    assert len(svc.audit_log) == 2
    first, second = svc.audit_log
    assert first["from_status"] == "draft"
    assert first["to_status"] == "pending_review"
    assert first["actor"] == "user-operator-001"
    assert second["from_status"] == "pending_review"
    assert second["to_status"] == "published"
    assert second["actor"] == "user-supervisor-001"
    for entry in svc.audit_log:
        assert entry["course_id"] == course.id
        assert entry["at"]


def test_status_changed_event_emitted(svc, store):
    course = make_course()
    store[course.id] = course
    transition(svc, store, course.id, OPERATOR, "submit")
    assert len(svc.event_sink) == 1
    event = svc.event_sink[0]
    assert event["type"] == "academy.course.status_changed"
    assert event["from_status"] == "draft"
    assert event["to_status"] == "pending_review"


def test_reject_audit_keeps_reason(svc, store):
    course = make_course()
    store[course.id] = course
    transition(svc, store, course.id, OPERATOR, "submit")
    transition(svc, store, course.id, SUPERVISOR, "reject", reason="缺课纲")
    assert svc.audit_log[-1]["reason"] == "缺课纲"
    assert svc.audit_log[-1]["to_status"] == "draft"


# -- AC4：缺少主讲或课期时提交失败并返回缺失项列表 ------------------------------

def test_submit_without_lecturers_fails_with_missing_list(svc, store):
    course = make_course(lecturers=[])
    store[course.id] = course
    status, body = transition(svc, store, course.id, OPERATOR, "submit")
    assert status == 422
    assert "lecturers" in body["missing"]
    assert course.status == "draft"


def test_submit_without_sessions_fails_with_missing_list(svc, store):
    course = make_course(sessions=[])
    store[course.id] = course
    status, body = transition(svc, store, course.id, OPERATOR, "submit")
    assert status == 422
    assert "sessions" in body["missing"]


def test_submit_missing_several_fields_lists_all(svc, store):
    course = make_course(title="", lecturers=[], sessions=[])
    store[course.id] = course
    status, body = transition(svc, store, course.id, OPERATOR, "submit")
    assert status == 422
    assert body["missing"] == ["title", "lecturers", "sessions"]


# -- 下架确认、驳回原因、published 编辑守卫 -------------------------------------

def test_offline_with_active_enrollments_requires_confirm(svc, store):
    course = published_course(svc, store)
    course.active_enrollments = 3
    status, body = transition(svc, store, course.id, OPERATOR, "offline")
    assert status == 422
    assert body["error_code"] == "ACAD_COURSE_CONFIRM_REQUIRED"
    assert body["affected_enrollments"] == 3
    assert course.status == "published"


def test_offline_with_confirm_returns_affected_count(svc, store):
    course = published_course(svc, store)
    course.active_enrollments = 3
    status, body = transition(svc, store, course.id, OPERATOR, "offline",
                              confirm=True)
    assert status == 200
    assert body["affected_enrollments"] == 3
    assert course.status == "offline"


def test_reject_without_reason_fails(svc, store):
    course = make_course()
    store[course.id] = course
    transition(svc, store, course.id, OPERATOR, "submit")
    status, body = transition(svc, store, course.id, SUPERVISOR, "reject",
                              reason="  ")
    assert status == 422
    assert body["error_code"] == "ACAD_COURSE_REASON_REQUIRED"
    assert course.status == "pending_review"


def test_published_allows_sort_weight_and_sessions(svc, store):
    course = published_course(svc, store)
    status, body = handle_update(svc, store, course.id, OPERATOR,
                                 {"sort_weight": 5})
    assert status == 200
    assert course.sort_weight == 5
    status, _ = handle_update(svc, store, course.id, OPERATOR,
                              {"sessions": ["2027-02-02 10:00"]})
    assert status == 200


def test_published_blocks_title_intro_lecturer_edit(svc, store):
    course = published_course(svc, store)
    for changes in ({"title": "新标题"}, {"intro": "新简介"},
                    {"lecturers": ["讲师乙"]}):
        status, body = handle_update(svc, store, course.id, OPERATOR, changes)
        assert status == 409
        assert body["error_code"] == "ACAD_COURSE_PUBLISHED_FIELD_LOCKED"
    assert course.title == "虚构课程：会员空间运营入门"


def test_archived_is_readonly(svc, store):
    course = published_course(svc, store)
    transition(svc, store, course.id, OPERATOR, "offline")
    transition(svc, store, course.id, OPERATOR, "archive")
    status, body = handle_update(svc, store, course.id, OPERATOR,
                                 {"sort_weight": 9})
    assert status == 409
    assert body["error_code"] == "ACAD_COURSE_ARCHIVED_READONLY"
