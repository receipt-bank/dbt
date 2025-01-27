from typing import List, Optional, Type

from dbt.config.project import Project
from dbt.adapters.base import BaseAdapter, Credentials


class AdapterPlugin:
    """Defines the basic requirements for a dbt adapter plugin.

    :param include_path: The path to this adapter plugin's root
    :param dependencies: A list of adapter names that this adapter depends
        upon.
    """
    def __init__(
        self,
        adapter: Type[BaseAdapter],
        credentials: Type[Credentials],
        include_path: str,
        dependencies: Optional[List[str]] = None
    ):
        self.adapter: Type[BaseAdapter] = adapter
        self.credentials: Type[Credentials] = credentials
        self.include_path: str = include_path
        project = Project.from_project_root(include_path, {})
        self.project_name: str = project.project_name
        self.dependencies: List[str]
        if dependencies is None:
            self.dependencies = []
        else:
            self.dependencies = dependencies
