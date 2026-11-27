# clawhunt-1884-academy-course-state

ClawHunt #1884 样品仓（任务编码 S6-ACAD-010）：会员空间平台 · 学院课程状态机与发布审批。

实现课程状态流转服务 `CourseStateService`：所有状态变化只能经它完成。
只用虚构测试数据，不含任何真实个人信息、企业信息与客户名称。

## 流转规则表

| 流转 | 动作 | 执行人 | 校验 / 说明 |
|---|---|---|---|
| draft → pending_review | submit 提交审核 | 运营专员 | 必填：标题、类别、简介、至少 1 名主讲、至少 1 个课期；缺失返回缺失项列表（422） |
| pending_review → published | approve 审批通过 | 运营主管 | 属对外发布 L2 审批；提交人不能审批自己提交的课程（403） |
| pending_review → draft | reject 驳回 | 运营主管 | 必须填写驳回原因，否则 422 |
| published → offline | offline 下架 | 运营专员 | 存在报名中课期时须传 confirm=true，并返回受影响报名数 |
| offline → pending_review | resubmit 重新上架 | 运营专员 | 需再次审批（回到 pending_review，不是直接 published） |
| offline → archived | archive 归档 | 运营专员 | 归档后只读，任何修改与流转均拒绝 |

其他规则：

- published 状态下只允许改 `sort_weight` 和课期；标题、简介、讲师等对外内容须先下架再修改，否则 409（`ACAD_COURSE_PUBLISHED_FIELD_LOCKED`）。
- 任一非法流转返回 409 与错误码 `ACAD_COURSE_INVALID_TRANSITION`。
- 每次流转写一条审计日志（操作人、前后状态、原因、时间），并发出领域事件 `academy.course.status_changed`。
- 接口：`POST /api/admin/academy/courses/{id}/transitions`，请求体 `{action, reason, confirm}`，action 取值 submit / approve / reject / offline / resubmit / archive。

## 运行与测试

```bash
bash setup.sh
```

一键自测分三步：1/3 pytest；2/3 变异自证；3/3 MANIFEST 核验。
全部通过打印 `SELFTEST: PASS`。首次运行会自动建仓内 `.venv` 并安装 pytest。

也可直接跑：

```bash
python3 -m pytest -q tests/                 # 27 个测试
python3 scripts/mutation_check.py           # 变异自证（3 个变异均须被测试抓住）
```

## 验收结果（本机实测，见 evidence/ 原始日志）

- pytest：27 passed（见 `evidence/pytest-20261127.log`）。
- 变异自证：MUTATION CHECK: PASS，3/3 变异均被对应测试抓住（见 `evidence/mutation-check-20261127.log`）。
- 一键复跑：SELFTEST: PASS（见 `evidence/selftest-setup-sh-20261127.log`）。

验收标准对照：

1. 6 条合法流转各有测试用例通过；任一非法流转返回 409 与 `ACAD_COURSE_INVALID_TRANSITION`——6 个合法流转测试 + 5 个非法流转测试（含归档后终端态）全部通过。
2. 提交人审批自己提交的课程时返回 403——`test_self_approval_returns_403` 通过。
3. 每次流转在审计日志中有一条含前后状态的记录——`test_audit_log_records_every_transition` 断言每条记录含操作人、前后状态与时间；驳回原因亦记入审计。
4. 缺少主讲或课期时提交审核失败，返回缺失项列表——3 个测试通过，缺失多项时一次性返回全部缺失项。

## 文件说明

- `src/course_state.py`：状态机服务本体（状态、合法流转表、校验、审计、事件、published 编辑守卫）。
- `src/api.py`：流转接口适配层，`handle_transition()` 返回 `(http_status, body)`，与约定接口的映射一致。
- `src/stubs.py`：S2-IAM / S2-AUDIT / 事件总线的内存桩与真实对接点说明。
- `tests/test_course_state.py`：27 个 pytest 测试。
- `scripts/mutation_check.py`：变异自证脚本。
- `MANIFEST`：交付文件 SHA256 清单（不含 MANIFEST 自身与自测输出日志 `evidence/selftest-setup-sh-20261127.log`——后者是自测的输出文件，写入即改变自身哈希，无法列入清单自证）。

## 生产接线（FastAPI）

`handle_transition()` 是约定接口的纯函数等价实现，生产时挂到 FastAPI 路由即可：

```python
from fastapi import APIRouter, Depends
from src.api import handle_transition
from src.course_state import Actor

router = APIRouter()

@router.post("/api/admin/academy/courses/{course_id}/transitions")
def transition_course(course_id: str, body: dict, actor: Actor = Depends(get_current_actor)):
    status, payload = handle_transition(service, store, course_id, actor,
                                        body["action"], body.get("reason", ""),
                                        body.get("confirm", False))
    return JSONResponse(status_code=status, content=payload)
```

其中 `service` / `store` 替换为 SQLAlchemy 会话与真实仓储，`get_current_actor`
走 S2-IAM 校验角色（approve 须 role == supervisor），审计日志经 S2-AUDIT
写入审计表；业务表统一含 id(UUID)、org_id、site_id、created_at、created_by、
updated_at、deleted_at（软删除），金额以「分」存整数，配置与密钥只从环境变量读取。

## 未做项（诚实记录）

- 未接真实 FastAPI / PostgreSQL / Redis：样品用内存模型与纯函数接口层，状态码与错误码映射与约定一致，生产接线见上节。
- 未接真实 S2-IAM 与 S2-AUDIT：用内存桩代替，对接点见 `src/stubs.py`。
- 未做前端与小程序侧：本单范围是状态机服务与流转接口。
