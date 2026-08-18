"""IAM 数据访问层（05 §11 `iam/`）。"""

from openviking.server.platform.iam.postgres_repository import PostgresIamRepository
from openviking.server.platform.iam.repository import IamRepository

__all__ = ["IamRepository", "PostgresIamRepository"]
