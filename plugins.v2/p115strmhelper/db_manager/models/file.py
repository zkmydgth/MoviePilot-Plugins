from typing import Dict, List

from sqlalchemy import (
    Column,
    Integer,
    String,
    Text,
    BigInteger,
    select,
    delete,
    text,
)
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from ...db_manager import db_update, db_query, P115StrmHelperBase


class File(P115StrmHelperBase):
    """
    文件类
    """

    __tablename__ = "files"

    id = Column(Integer, primary_key=True)
    parent_id = Column(Integer, nullable=False)
    name = Column(String(255), default="")
    sha1 = Column(String(40), default="")
    size = Column(BigInteger, default=0)
    pickcode = Column(String(50), default="")
    ctime = Column(BigInteger, default=0)
    mtime = Column(BigInteger, default=0)
    path = Column(Text, unique=True)
    extra = Column(Text)

    @staticmethod
    @db_query
    def get_by_path(db: Session, file_path: str):
        """
        通过路径获取（当路径不唯一报错 MultipleResultsFound）
        """
        return db.execute(
            select(File).where(File.path == file_path)
        ).scalar_one_or_none()

    @staticmethod
    @db_query
    def get_by_id(db: Session, file_id: int):
        """
        通过ID获取
        """
        return db.scalars(select(File).where(File.id == file_id)).first()

    @staticmethod
    @db_query
    def get_by_parent_id(db: Session, parent_id: int):
        """
        通过parent_id获取
        """
        return (
            db.execute(select(File).where(File.parent_id == parent_id)).scalars().all()
        )

    @staticmethod
    @db_update
    def delete_by_path(db: Session, file_path: str):
        """
        通过路径删除（删除所有匹配值）
        """
        db.execute(delete(File).where(File.path == file_path))
        return True

    @staticmethod
    @db_update
    def delete_by_id(db: Session, file_id: int):
        """
        通过ID删除
        """
        db.execute(delete(File).where(File.id == file_id))
        return True

    @staticmethod
    @db_update
    def upsert_batch(db: Session, batch: List[Dict]):
        """
        批量写入或更新数据
        """
        files_data_map = {
            entry["data"]["id"]: entry["data"]
            for entry in batch
            if entry.get("table") == "files" and "id" in entry.get("data", {})
        }

        if not files_data_map:
            return True

        files_data = list(files_data_map.values())

        db.execute(text("PRAGMA synchronous = OFF"))
        db.execute(text("PRAGMA journal_mode = MEMORY"))

        stmt = sqlite_insert(File).prefix_with("OR REPLACE").values(files_data)
        db.execute(stmt)
        return True

    @staticmethod
    @db_update
    def upsert_batch_by_list(db: Session, batch: List[Dict]):
        """
        通过列表批量写入或更新数据
        """
        db.execute(text("PRAGMA synchronous = OFF"))
        db.execute(text("PRAGMA journal_mode = MEMORY"))

        stmt = sqlite_insert(File).prefix_with("OR REPLACE").values(batch)
        db.execute(stmt)
        return True

    @staticmethod
    @db_update
    def remove_by_path_batch(db: Session, path: str):
        """
        通过路径批量删除
        """
        db.execute(delete(File).where(File.path.startswith(path)))
        return True

    @staticmethod
    @db_update
    def update_path(db: Session, file_id: int, new_path: str):
        """
        更新指定ID的路径
        """
        db.query(File).filter(File.id == file_id).update(
            {"path": new_path}, synchronize_session=False
        )

    @staticmethod
    @db_update
    def update_name(db: Session, file_id: int, new_name: str):
        """
        更新指定ID的名称
        """
        db.query(File).filter(File.id == file_id).update({"name": new_name})
