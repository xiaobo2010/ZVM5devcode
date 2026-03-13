#2026.3.13 Workflow部分API逻辑

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Dict, Optional, Union
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Path
from pydantic import BaseModel, Field, ConfigDict


app = FastAPI(
    title="ZVP5 Workflow API",
    version="0.1.0",
    description="ZVP5 Workflow V1 by XiaoBo2010",
)

#定义Enums
class WorkflowType(str, Enum):
    create = "create"
    update = "update"
    trash = "trash"


class WorkflowAction(str, Enum):
    approve = "approve"
    reject = "reject"
    deny = "deny"
    update = "update"


class WorkflowStatus(str, Enum):
    pending = "pending"
    approved = "approved"
    rejected = "rejected"
    denied = "denied"
    needs_update = "needs_update"


#Activity Models
class ActivityCreate(BaseModel):
    """
    新建的完整活动对象，此处我瞎写的名字，后面再改
    """
    title: str = Field(..., description="活动标题")
    description: Optional[str] = Field(None, description="活动描述")
    location: Optional[str] = Field(None, description="地点")
    start_at: Optional[datetime] = Field(None, description="开始时间")
    end_at: Optional[datetime] = Field(None, description="结束时间")
    max_participants: Optional[int] = Field(None, ge=1, description="人数上限")


class ActivityPartition(BaseModel):
    """
    局部更新活动时提交的变化部分（Partial Update）
    """
    title: Optional[str] = None
    description: Optional[str] = None
    location: Optional[str] = None
    start_at: Optional[datetime] = None
    end_at: Optional[datetime] = None
    max_participants: Optional[int] = Field(None, ge=1)


#Request Model部分
class CreateWorkflowRequest(BaseModel):
    """
    创建 workflow：
    - type=create 时，activity=ActivityCreate
    - type=update/trash 时，activity =string（活动 id）
    """
    type: WorkflowType
    activity: Union[ActivityCreate, str]


class WorkflowReviewPayload(BaseModel):
    changes: Optional[ActivityPartition] = None
    comment: Optional[str] = Field(None, description="审核意见")
  #没准审核意见可以再换掉

class ReviewWorkflowRequest(BaseModel):
    action: WorkflowAction
    update: WorkflowReviewPayload = Field(default_factory=WorkflowReviewPayload)


#respond和record部分

class WorkflowRecord(BaseModel):
    model_config = ConfigDict(use_enum_values=True)

    id: str
    type: WorkflowType
    status: WorkflowStatus
    created_at: datetime
    updated_at: datetime

    # Request type定义:
    # create: ActivityCreate
    # update/trash: str(activity_id)
    activity: Union[ActivityCreate, str]

    # 审核相关内容
    review_action: Optional[WorkflowAction] = None
    review_comment: Optional[str] = None
    requested_changes: Optional[ActivityPartition] = None

# 没有数据库，先写在内存里，后面再补充

# TODO: 后续替换成真正的数据库 / repository 层
WORKFLOWS: Dict[str, WorkflowRecord] = {}

# Helper

def ensure_create_workflow_payload(req: CreateWorkflowRequest) -> None:
    """
    校验 create/update/trash 三种类型下 activity 字段的形态是否正确
    """
    if req.type == WorkflowType.create:
        if not isinstance(req.activity, ActivityCreate):
            raise HTTPException(
                status_code=422,
                detail="当 type=create 时，activity 必须是完整的 Activity 对象"
            )
    else:
        if not isinstance(req.activity, str):
            raise HTTPException(
                status_code=422,
                detail="当 type=update 或 type=trash 时，activity 必须是活动 id（string）"
            )


def validate_review_request(req: ReviewWorkflowRequest) -> None:
    """
    根据 action 校验 changes 是否符合预期
    """
    if req.action in (WorkflowAction.approve, WorkflowAction.deny):
        if req.update.changes is not None:
            raise HTTPException(
                status_code=422,
                detail=f"当 action={req.action.value} 时，update.changes 必须为 null"
            )

    if req.action == WorkflowAction.update:
        if req.update.changes is None:
            raise HTTPException(
                status_code=422,
                detail="当 action=update 时，update.changes 不能为空"
            )

    # reject 默认允许 comment-only，不强制 changes


def next_status_from_action(action: WorkflowAction) -> WorkflowStatus:
    if action == WorkflowAction.approve:
        return WorkflowStatus.approved
    if action == WorkflowAction.reject:
        return WorkflowStatus.rejected
    if action == WorkflowAction.deny:
        return WorkflowStatus.denied
    if action == WorkflowAction.update:
        return WorkflowStatus.needs_update
    raise ValueError(f"Unknown action: {action}")


#FastAPI路由部分

@app.post("/api/v1/workflows", response_model=WorkflowRecord, tags=["workflow"])
def create_workflow(req: CreateWorkflowRequest):
    """
    创建一个义工申请的审核
    """
    ensure_create_workflow_payload(req)

    workflow_id = str(uuid4())
    now = datetime.utcnow()

    record = WorkflowRecord(
        id=workflow_id,
        type=req.type,
        status=WorkflowStatus.pending,
        created_at=now,
        updated_at=now,
        activity=req.activity,
        review_action=None,
        review_comment=None,
        requested_changes=None,
    )

    # TODO: record写入数据库部分未实现
    WORKFLOWS[workflow_id] = record

    return record


@app.post("/api/v1/workflows/{id}", response_model=WorkflowRecord, tags=["workflow"])
def review_workflow(
    req: ReviewWorkflowRequest,
    id: str = Path(..., description="workflow id"),
):
    """
    审核某个 workflow：
    - approve: 通过
    - reject: 打回
    - deny: 拒绝
    - update: 要求进一步修改
    """
    validate_review_request(req)

    record = WORKFLOWS.get(id)
    if record is None:
        raise HTTPException(status_code=404, detail="workflow 不存在")

    # 锁定workflow不允许再次审核，把这段代码取消注释! :/
    # if record.status != WorkflowStatus.pending and record.status != WorkflowStatus.needs_update:
    #     raise HTTPException(status_code=409, detail="当前 workflow 状态不允许再次审核")

    record.status = next_status_from_action(req.action)
    record.review_action = req.action
    record.review_comment = req.update.comment
    record.requested_changes = req.update.changes
    record.updated_at = datetime.utcnow()

    # TODO:
    # 1. 如果 action=approve，要写入现在不存在的后端数据库：
    #    - type=create -> 创建活动
    #    - type=update -> 更新活动
    #    - type=trash  -> 删除/归档活动
    #
    # 2. 如果 action=update/reject/deny，需要记录审核日志并累加义工。

    WORKFLOWS[id] = record
    return record


@app.get("/api/v1/workflows/{id}", response_model=WorkflowRecord, tags=["workflow"])
def get_workflow(id: str):
    """
    便于debug,此处查看义工的workflow已展开了
    """
    record = WORKFLOWS.get(id)
    if record is None:
        raise HTTPException(status_code=404, detail="workflow 不存在")
    return record


@app.get("/healthz", tags=["system"])
def healthz():
    return {"ok": True}
