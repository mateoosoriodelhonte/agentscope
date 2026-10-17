"""Datasets. Import and validation take the raw JSON document as the request body and parse
it with the same hardened loader as the CLI, so identical strictness rules apply."""

from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse, Response

from agentscope.api.deps import InvalidInput, ServicesDep, download
from agentscope.api.schemas import DatasetDetail, DatasetSummary, DatasetValidation
from agentscope.datasets.io import export_dataset, export_filename
from agentscope.datasets.issues import DatasetError
from agentscope.datasets.loader import load_dataset_bytes
from agentscope.services import validation_report
from agentscope.storage.repository import DatasetConflict

router = APIRouter(prefix="/api/datasets", tags=["datasets"])

_RAW_DATASET_BODY = {
    "requestBody": {
        "required": True,
        "content": {"application/json": {"schema": {"$ref": "#/components/schemas/Dataset"}}},
    }
}


@router.get("")
def list_datasets(services: ServicesDep) -> list[DatasetSummary]:
    return services.repository.list_datasets()


@router.post(
    "",
    status_code=201,
    openapi_extra=_RAW_DATASET_BODY,
    responses={409: {"description": "name@version exists"}, 422: {"description": "invalid"}},
)
async def import_dataset(request: Request, services: ServicesDep) -> DatasetSummary:
    body = await request.body()
    try:
        # Parsing up to max_request_bytes is CPU work: keep it off the event loop.
        dataset = await run_in_threadpool(load_dataset_bytes, body)
        return await run_in_threadpool(services.import_dataset, dataset)
    except DatasetError as exc:
        raise InvalidInput("the dataset is invalid", exc.issues) from exc
    except DatasetConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/validate", openapi_extra=_RAW_DATASET_BODY)
async def validate_dataset(request: Request, services: ServicesDep) -> DatasetValidation:
    """Lints an untrusted document without importing it: problems are reported, not raised."""
    body = await request.body()
    try:
        dataset = await run_in_threadpool(load_dataset_bytes, body)
    except DatasetError as exc:
        return validation_report(None, exc.issues)
    return validation_report(dataset, await run_in_threadpool(services.validate_dataset, dataset))


def _detail(services: ServicesDep, name: str, version: str) -> DatasetDetail:
    detail = services.repository.get_dataset(name, version)
    if detail is None:
        raise HTTPException(status_code=404, detail="dataset not found")
    return detail


@router.get("/{name}/{version}", response_model=DatasetDetail)
def get_dataset(name: str, version: str, services: ServicesDep) -> JSONResponse:
    """`dataset` is the export document, so it can be posted back to POST /api/datasets or
    /validate unchanged (e.g. to fork it as a new version). A plain dump would add
    `"equals": null` next to each argument matcher's comparison, which counts as a second
    one and makes the document invalid."""
    detail = _detail(services, name, version)
    return JSONResponse(
        {
            "summary": detail.summary.model_dump(mode="json"),
            "dataset": json.loads(export_dataset(detail.dataset)),
        }
    )


@router.get("/{name}/{version}/export", response_class=Response)
def export(name: str, version: str, services: ServicesDep) -> Response:
    dataset = _detail(services, name, version).dataset
    return download(export_dataset(dataset), export_filename(dataset))
