from datetime import datetime
from sqlalchemy import ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from src.config.database import Base
from src.constants.enums import Cleanup
from src.utils.time import now

class OwnerCorrection(Base):
    """计量室登记的批次内「外协姓名 → 正确班组」对应关系，按批次名称生效。"""
    __tablename__ = 'owner_corrections'
    __table_args__ = (UniqueConstraint('dataset_name', 'old_name'),)

    id: Mapped[int] = mapped_column(primary_key=True)
    dataset_name: Mapped[str] = mapped_column(String(120), index=True)
    old_name: Mapped[str] = mapped_column(String(60))
    new_name: Mapped[str] = mapped_column(String(60))
    author: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(default=now)

class CleanupTask(Base):
    """单个数据批次的清理任务：版本驱动重试，完成前批次暂停查看。"""
    __tablename__ = 'cleanup_tasks'

    id: Mapped[int] = mapped_column(primary_key=True)
    dataset_id: Mapped[int] = mapped_column(ForeignKey('datasets.id'), unique=True)
    state: Mapped[str] = mapped_column(String(20), default=Cleanup.CLEANING, index=True)
    version: Mapped[int] = mapped_column(default=1)
    attempt: Mapped[int] = mapped_column(default=0)
    lease_until: Mapped[datetime | None]
    author: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(default=now)
    finished_at: Mapped[datetime | None]
