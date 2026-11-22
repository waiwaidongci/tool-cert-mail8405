"""后台清理：改写批次原始行、历史班报与导出文件，旧副本清完才恢复查看。"""

import logging
from datetime import timedelta
from sqlalchemy import or_, select
from src.config.database import Session
from src.config.settings import ARTIFACTS
from src.constants.enums import Cleanup
from src.models.corrections import CleanupTask
from src.models.jobs import Run
from src.models.source import Dataset
from src.services.audit import record
from src.services.corrections import mapping_for, sanitize_result, sanitize_rows
from src.services.datasets import checksum
from src.utils.exports import write_report
from src.utils.time import now

log = logging.getLogger(__name__)

MAX_ATTEMPTS = 5
LEASE_MINUTES = 3


def clean_next():
    with Session.begin() as db:
        stamp = now()
        task = db.scalar(select(CleanupTask)
                         .where(CleanupTask.state == Cleanup.CLEANING,
                                or_(CleanupTask.lease_until.is_(None), CleanupTask.lease_until < stamp))
                         .order_by(CleanupTask.id).limit(1).with_for_update(skip_locked=True))
        if task is None:
            return
        if task.attempt >= MAX_ATTEMPTS:
            task.state = Cleanup.FAILED
            record(db, 'worker', 'cleanup.failed', task.dataset_id, reason='后台清理多次中断，请重新登记纠正')
            return
        task.attempt += 1
        task.lease_until = stamp + timedelta(minutes=LEASE_MINUTES)
        db.flush()
        claimed = (task.id, task.dataset_id, task.version, task.attempt)
    task_id, dataset_id, version, attempt = claimed
    try:
        exports = _rewrite(dataset_id)
        _sweep_exports(exports)
    except Exception:
        # 中断后租约立即释放，下一轮按同一映射重试；各步骤幂等，不会重复替换。
        log.exception('Cleanup task %s interrupted', task_id)
        with Session.begin() as db:
            task = db.get(CleanupTask, task_id)
            if task and task.version == version:
                task.lease_until = None
        return
    with Session.begin() as db:
        task = db.get(CleanupTask, task_id, with_for_update=True)
        if task is None or task.state != Cleanup.CLEANING:
            return
        if task.version != version:
            # 清理期间登记了新的纠正，立即按新映射再清一轮。
            task.lease_until = None
            return
        task.state = Cleanup.DONE
        task.lease_until = None
        task.finished_at = now()
        record(db, 'worker', 'cleanup.done', dataset_id, attempt=attempt)


def _rewrite(dataset_id):
    """改写数据批次与历史班报，返回需要重写的导出文件清单。"""
    with Session.begin() as db:
        dataset = db.get(Dataset, dataset_id)
        if dataset is None:
            return []
        mapping = mapping_for(db, dataset.name)
        dataset.rows = sanitize_rows(dataset.rows, mapping)
        dataset.row_count = len(dataset.rows)
        signature = checksum(dataset.rows)
        clash = db.scalar(select(Dataset.id).where(Dataset.name == dataset.name,
                                                   Dataset.checksum == signature,
                                                   Dataset.id != dataset.id))
        if clash is None:
            dataset.checksum = signature
        runs = db.scalars(select(Run).where(Run.dataset_id == dataset_id)).all()
        for run in runs:
            if run.result:
                run.result = sanitize_result(run.result, mapping)
        return [(run.id, run.attempt, run.export_file, run.result) for run in runs if run.export_file]


def _sweep_exports(exports):
    """按清理后的班报重写导出 CSV，并删除历次尝试留下的旧副本。"""
    for run_id, attempt, export_file, result in exports:
        current = export_file
        if result is not None:
            current = write_report(run_id, attempt, result)
        for stale in ARTIFACTS.glob(f'analysis-{run_id}-*.csv'):
            if stale.name != current:
                stale.unlink(missing_ok=True)
