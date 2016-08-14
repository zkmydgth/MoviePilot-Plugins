from typing import Dict, List

from sqlalchemy import Column, Integer, String, Text, select, delete, or_
from sqlalchemy.orm import Session

from ...db_manager import db_update, db_query, P115StrmHelperBase


class Folder(P115StrmHelperBase):
    """
    文件夹类
    """

    __tablename__ = "folders"

    id = Column(Integer, primary_key=True)
    parent_id = Column(Integer, nullable=False)
    name = Column(String(255), nullable=False)
    path = Column(Text, nullable=False)

    @staticmethod
    @db_query
    def get_by_path(db: Session, file_path: str):
        """
        通过路径获取（当路径不唯一报错 MultipleResultsFound）
        """
        return db.execute(
            select(Folder).where(Folder.path == file_path)
        ).scalar_one_or_none()

    @staticmethod
    @db_query
    def get_by_id(db: Session, file_id: int):
        """
        通过ID获取
        """
        return db.scalars(select(Folder).where(Folder.id == file_id)).first()

    @staticmethod
    @db_query
    def get_by_parent_id(db: Session, parent_id: int):
        """
        通过parent_id获取
        """
        return (
            db.execute(select(Folder).where(Folder.parent_id == parent_id))
            .scalars()
            .all()
        )

    @db_update
    def delete_by_path(self, db: Session, file_path: str):
        """
        通过路径删除（删除所有匹配值）
        """
        db.execute(delete(Folder).where(Folder.path == file_path))
        return True

    @db_update
    def delete_by_id(self, db: Session, file_id: int):
        """
        通过ID删除
        """
        data = self.get_by_id(db, file_id)
        if data:
            data.delete(db, data.id)
        return True

    @staticmethod
    @db_update
    def upsert_batch(db: Session, batch: List[Dict]):
        """
        批量写入或更新数据

        逻辑：
          - 先判断需要写入的数据路径是否存在，存在则先删除记录
          - 写入数据
        """
        folders_data = []
        seen_ids = set()
        seen_paths = set()
        append = folders_data.append
        for entry in batch:
            if entry["table"] == "folders":
                data = entry["data"]
                if data["id"] not in seen_ids:
                    seen_ids.add(data["id"])
                    seen_paths.add(data["path"])
                    append(data)
        if not folders_data:
            return True
        db.execute(
            delete(Folder).where(
                or_(Folder.id.in_(seen_ids), Folder.path.in_(seen_paths))
            )
        )
        db.bulk_insert_mappings(Folder, folders_data)
        return True

    @staticmethod
    @db_update
    def remove_by_path_batch(db: Session, path: str):
        """
        通过路径批量删除
        """
        db.query(Folder).filter(Folder.path.startswith(path)).delete(
            synchronize_session=False
        )
        return True
