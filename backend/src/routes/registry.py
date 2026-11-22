from urllib.parse import quote
from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import select
from sqlalchemy.orm import Session
from src.config.database import connection
from src.constants.enums import Cleanup, Role
from src.controllers.auth import allow, current
from src.domain.spec import SPEC
from src.models.corrections import CleanupTask, OwnerCorrection
from src.models.identity import User
from src.models.source import Dataset
from src.schemas.requests import CorrectionRequest, Upload
from src.services.audit import record
from src.services.corrections import cleaning_ids, mapping_for, sanitize_rows
from src.services.datasets import save, template
from src.services.validation import parse_rows
from src.utils import views
from src.utils.time import iso
from src.utils.wakeup import wake

router = APIRouter()

BLOCKED = '该批次正在清理班组信息，请稍后查看'


@router.get('/spec')
def spec():
    return {key: value for key, value in SPEC.items() if key != 'fixture'}


@router.get('/template')
def sample(actor: User = Depends(current)):
    filename = quote(SPEC['sample_name'])
    return Response(template(), media_type='text/csv; charset=utf-8',
                    headers={'Content-Disposition': f"attachment; filename*=UTF-8''{filename}"})


@router.get('/datasets')
def datasets(db: Session = Depends(connection), actor: User = Depends(current)):
    blocked = cleaning_ids(db)
    return [views.dataset(item, cleaning=item.id in blocked)
            for item in db.scalars(select(Dataset).order_by(Dataset.id.desc()).limit(100))]


@router.get('/datasets/{dataset_id}')
def details(dataset_id: int, offset: int = Query(0, ge=0), limit: int = Query(16, ge=1, le=100),
            db: Session = Depends(connection), actor: User = Depends(current)):
    item = db.get(Dataset, dataset_id)
    if not item:
        raise HTTPException(404, '找不到该数据批次')
    if dataset_id in cleaning_ids(db):
        raise HTTPException(409, BLOCKED)
    return {**views.dataset(item), 'rows': item.rows[offset:offset + limit]}


@router.post('/datasets', status_code=201)
def upload(body: Upload, db: Session = Depends(connection),
           actor: User = Depends(allow(Role.ADMIN, Role.ANALYST))):
    rows = parse_rows(body.csv_text)
    # 重传原文件时套用已登记的纠正，旧姓名不再入库。
    rows = sanitize_rows(rows, mapping_for(db, body.name))
    item, created = save(db, body.name, body.filename, rows, actor.username)
    if created:
        record(db, actor.username, 'dataset.imported', item.id, rows=item.row_count)
    db.commit()
    return {**views.dataset(item), 'created': created}


def correction_view(db, item):
    rows = db.scalars(select(OwnerCorrection).where(OwnerCorrection.dataset_name == item.name)
                      .order_by(OwnerCorrection.id)).all()
    task = db.scalar(select(CleanupTask).where(CleanupTask.dataset_id == item.id))
    return {'dataset_id': item.id,
            'corrections': [{'old_name': row.old_name, 'new_name': row.new_name, 'author': row.author,
                             'created_at': iso(row.created_at)} for row in rows],
            'cleanup': {'state': task.state, 'attempt': task.attempt} if task else None}


@router.get('/datasets/{dataset_id}/corrections')
def corrections(dataset_id: int, db: Session = Depends(connection), actor: User = Depends(current)):
    item = db.get(Dataset, dataset_id)
    if not item:
        raise HTTPException(404, '找不到该数据批次')
    return correction_view(db, item)


@router.post('/datasets/{dataset_id}/corrections', status_code=201)
def correct(dataset_id: int, body: CorrectionRequest, db: Session = Depends(connection),
            actor: User = Depends(allow(Role.ADMIN, Role.ANALYST))):
    item = db.get(Dataset, dataset_id)
    if not item:
        raise HTTPException(404, '找不到该数据批次')
    submitted = {}
    for entry in body.corrections:
        if entry.old_name == entry.new_name:
            raise ValueError(f'姓名 {entry.old_name} 纠正前后相同')
        if entry.old_name in submitted:
            raise ValueError(f'姓名 {entry.old_name} 重复提交')
        submitted[entry.old_name] = entry.new_name
    existing = {row.old_name: row for row in
                db.scalars(select(OwnerCorrection).where(OwnerCorrection.dataset_name == item.name))}
    merged = {**{old: row.new_name for old, row in existing.items()}, **submitted}
    for old, new in merged.items():
        if new in merged:
            raise ValueError(f'班组 {new} 同时是另一条纠正的姓名，请调整对应关系后再提交')
    for old, new in submitted.items():
        row = existing.get(old)
        if row:
            row.new_name = new
        else:
            db.add(OwnerCorrection(dataset_name=item.name, old_name=old, new_name=new,
                                   author=actor.username))
    # 同一批次名称的所有数据批次都按该映射重新清理。
    for dataset in db.scalars(select(Dataset).where(Dataset.name == item.name)):
        task = db.scalar(select(CleanupTask).where(CleanupTask.dataset_id == dataset.id))
        if task is None:
            db.add(CleanupTask(dataset_id=dataset.id, author=actor.username))
        else:
            task.state = Cleanup.CLEANING
            task.version += 1
            task.attempt = 0
            task.lease_until = None
            task.finished_at = None
    record(db, actor.username, 'correction.registered', item.id, names=len(submitted))
    db.commit()
    wake()
    return correction_view(db, item)
