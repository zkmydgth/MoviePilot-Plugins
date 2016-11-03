from pydantic import BaseModel

class MachineID(BaseModel):
    """
    machine id 数据
    """
    machine_id: str