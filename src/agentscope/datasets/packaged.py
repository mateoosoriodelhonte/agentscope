"""Datasets shipped inside the package (``agentscope/datasets/builtin/*.json``)."""

from __future__ import annotations

from dataclasses import replace
from importlib import resources

from agentscope.datasets.issues import DatasetError
from agentscope.datasets.loader import load_dataset_bytes
from agentscope.domain.datasets import Dataset


class UnknownDatasetError(LookupError):
    pass


def builtin_datasets() -> list[Dataset]:
    """Every packaged dataset, sorted by name.

    They go through the same hardened loader as user input, so a broken built-in fails
    loudly instead of being trusted.
    """
    package = resources.files("agentscope.datasets.builtin")
    datasets = []
    for entry in sorted(package.iterdir(), key=lambda e: e.name):
        if not (entry.is_file() and entry.name.endswith(".json")):
            continue
        try:
            datasets.append(load_dataset_bytes(entry.read_bytes()))
        except DatasetError as exc:
            raise DatasetError([replace(i, source=entry.name) for i in exc.issues]) from None
    return sorted(datasets, key=lambda d: d.name)


def load_builtin_dataset(name: str) -> Dataset:
    datasets = builtin_datasets()
    for dataset in datasets:
        if dataset.name == name:
            return dataset
    raise UnknownDatasetError(
        f"no built-in dataset {name!r}; available: {[d.name for d in datasets]}"
    )
