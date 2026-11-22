"""外协姓名纠正的查询与套用：导入、计算和清理共用同一套映射。"""

import re
from sqlalchemy import select
from src.constants.enums import Cleanup
from src.models.corrections import CleanupTask, OwnerCorrection


def mapping_for(db, dataset_name):
    """批次名称对应的「外协姓名 → 正确班组」映射。"""
    rows = db.scalars(select(OwnerCorrection).where(OwnerCorrection.dataset_name == dataset_name))
    return {row.old_name: row.new_name for row in rows}


def cleaning_ids(db):
    """仍在清理（含清理失败待处理）的数据批次编号，这些批次暂停查看。"""
    return set(db.scalars(select(CleanupTask.dataset_id).where(CleanupTask.state != Cleanup.DONE)))


def _replace_tokens(text, mapping):
    # 单趟替换：同一轮里纠正结果不会再次被匹配，重试时旧姓名已不存在，天然幂等。
    pattern = re.compile('|'.join(re.escape(name) for name in sorted(mapping, key=len, reverse=True)))
    return pattern.sub(lambda match: mapping[match.group(0)], text)


def sanitize_rows(rows, mapping):
    """改写导入行的责任班组，只动 owner 字段，证书与日期列保持不变。"""
    if not mapping:
        return rows
    return [{**row, 'owner': mapping[row['owner']]} if row.get('owner') in mapping else row
            for row in rows]


def sanitize_result(result, mapping):
    """改写班报结果里的责任班组与班组处置措辞，指标、证书判定和图表保持不变。"""
    if not mapping or not result:
        return result
    tables = []
    for table in result.get('tables', []):
        tables.append({**table, 'rows': [_sanitize_table_row(row, mapping) for row in table.get('rows', [])]})
    return {**result, 'tables': tables}


def _sanitize_table_row(row, mapping):
    row = dict(row)
    owner = row.get('owner')
    if owner in mapping:
        row['owner'] = mapping[owner]
    action = row.get('action')
    if isinstance(action, str):
        row['action'] = _replace_tokens(action, mapping)
    return row
