from sqlalchemy import Column, String, Integer, Sequence
from sqlalchemy.orm import Session

from ...db_manager import db_update, db_query, CloudTerminatorBase


class U115_Strm_Files(CloudTerminatorBase):
    # ID
    id = Column(Integer, Sequence('id'), primary_key=True, index=True)
    # 文件路径
    file_path = Column(String, primary_key=True, index=True)
    # 文件内容
    content = Column(String)

    @staticmethod
    @db_update
    def add_file(db: Session, **kwargs):
        db.add(U115_Strm_Files(**kwargs))

    @staticmethod
    @db_query
    def get_file_by_path(db: Session, file_path: str):
        return db.query(U115_Strm_Files).filter(U115_Strm_Files.file_path == file_path).first()

    @staticmethod
    @db_update
    def update_file_by_path(db: Session, file_path: str, key: str, value: any):
        db.query(U115_Strm_Files).filter(U115_Strm_Files.file_path == file_path).first.update({key: value})

    @staticmethod
    @db_update
    def delete_file_by_path(db: Session, file_path: str):
        db.query(U115_Strm_Files).filter(U115_Strm_Files.file_path == file_path).first.delete()
