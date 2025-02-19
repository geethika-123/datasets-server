# SPDX-License-Identifier: Apache-2.0
# Copyright 2022 The HuggingFace Authors.

import io
from contextlib import contextmanager
from dataclasses import replace
from fnmatch import fnmatch
from http import HTTPStatus
from typing import Any, Callable, Iterator, List, Optional

import datasets.builder
import datasets.info
import pandas as pd
import pytest
import requests
from datasets import Audio, Features, Image, Value, load_dataset_builder
from huggingface_hub.hf_api import HfApi
from libcommon.dataset import get_dataset_info_for_supported_datasets
from libcommon.exceptions import (
    CustomError,
    DatasetInBlockListError,
    DatasetManualDownloadError,
    DatasetTooBigFromDatasetsError,
    DatasetTooBigFromHubError,
    DatasetWithTooBigExternalFilesError,
    DatasetWithTooManyExternalFilesError,
)
from libcommon.processing_graph import ProcessingGraph
from libcommon.resources import CacheMongoResource, QueueMongoResource
from libcommon.simple_cache import CachedArtifactError, upsert_response
from libcommon.utils import Priority

from worker.config import AppConfig
from worker.job_runners.config.parquet_and_info import (
    ConfigParquetAndInfoJobRunner,
    get_writer_batch_size,
    parse_repo_filename,
    raise_if_blocked,
    raise_if_requires_manual_download,
    raise_if_too_big_from_datasets,
    raise_if_too_big_from_external_data_files,
    raise_if_too_big_from_hub,
)
from worker.resources import LibrariesResource

from ...constants import CI_HUB_ENDPOINT, CI_USER_TOKEN
from ...fixtures.hub import HubDatasetTest


@contextmanager
def blocked(app_config: AppConfig, repo_id: str) -> Iterator[None]:
    app_config.parquet_and_info.blocked_datasets.append(repo_id)
    yield
    app_config.parquet_and_info.blocked_datasets.remove(repo_id)


GetJobRunner = Callable[[str, str, AppConfig], ConfigParquetAndInfoJobRunner]


@pytest.fixture
def get_job_runner(
    libraries_resource: LibrariesResource,
    cache_mongo_resource: CacheMongoResource,
    queue_mongo_resource: QueueMongoResource,
) -> GetJobRunner:
    def _get_job_runner(
        dataset: str,
        config: str,
        app_config: AppConfig,
    ) -> ConfigParquetAndInfoJobRunner:
        processing_step_name = ConfigParquetAndInfoJobRunner.get_job_type()
        processing_graph = ProcessingGraph(
            {
                "dataset-level": {"input_type": "dataset"},
                processing_step_name: {
                    "input_type": "dataset",
                    "job_runner_version": ConfigParquetAndInfoJobRunner.get_job_runner_version(),
                    "triggered_by": "dataset-level",
                },
            }
        )
        return ConfigParquetAndInfoJobRunner(
            job_info={
                "type": ConfigParquetAndInfoJobRunner.get_job_type(),
                "params": {
                    "dataset": dataset,
                    "revision": "revision",
                    "config": config,
                    "split": None,
                },
                "job_id": "job_id",
                "priority": Priority.NORMAL,
            },
            app_config=app_config,
            processing_step=processing_graph.get_processing_step(processing_step_name),
            hf_datasets_cache=libraries_resource.hf_datasets_cache,
        )

    return _get_job_runner


def assert_content_is_equal(content: Any, expected: Any) -> None:
    print(content)
    assert set(content) == {"parquet_files", "dataset_info"}, content
    assert content["parquet_files"] == expected["parquet_files"], content
    assert len(content["dataset_info"]) == len(expected["dataset_info"]), content
    content_value = content["dataset_info"]
    expected_value = expected["dataset_info"]
    assert set(content_value.keys()) == set(expected_value.keys()), content
    for key in content_value.keys():
        if key != "download_checksums":
            assert content_value[key] == expected_value[key], content


def test_compute(
    app_config: AppConfig,
    get_job_runner: GetJobRunner,
    hub_reponses_public: HubDatasetTest,
) -> None:
    dataset = hub_reponses_public["name"]
    config = hub_reponses_public["config_names_response"]["config_names"][0]["config"]
    upsert_response(
        "dataset-config-names",
        dataset=dataset,
        http_status=HTTPStatus.OK,
        content=hub_reponses_public["config_names_response"],
    )
    job_runner = get_job_runner(dataset, config, app_config)
    response = job_runner.compute()
    assert response
    content = response.content
    assert content
    assert len(content["parquet_files"]) == 1
    assert_content_is_equal(content, hub_reponses_public["parquet_and_info_response"])


def test_compute_legacy_configs(
    app_config: AppConfig,
    get_job_runner: GetJobRunner,
    hub_public_legacy_configs: str,
) -> None:
    app_config = replace(app_config, parquet_and_info=replace(app_config.parquet_and_info, max_dataset_size=20_000))

    dataset_name = hub_public_legacy_configs
    original_configs = {"first", "second"}
    upsert_response(
        kind="dataset-config-names",
        dataset=hub_public_legacy_configs,
        http_status=HTTPStatus.OK,
        content={
            "config_names": [
                {"dataset": hub_public_legacy_configs, "config": "first"},
                {"dataset": hub_public_legacy_configs, "config": "second"},
            ],
        },
    )
    # first compute and push parquet files for each config for dataset with script with two configs
    for config in original_configs:
        job_runner = get_job_runner(dataset_name, config, app_config)
        assert job_runner.compute()
    hf_api = HfApi(endpoint=CI_HUB_ENDPOINT, token=CI_USER_TOKEN)
    dataset_info = hf_api.dataset_info(
        repo_id=hub_public_legacy_configs, revision=app_config.parquet_and_info.target_revision, files_metadata=False
    )
    repo_files = {f.rfilename for f in dataset_info.siblings}
    # assert that there are only parquet files for dataset's configs and ".gitattributes" in a repo
    # (no files from 'main')
    assert ".gitattributes" in repo_files
    assert all(
        fnmatch(file, "first/*.parquet") or fnmatch(file, "second/*.parquet")
        for file in repo_files.difference({".gitattributes"})
    )
    orig_repo_configs = {f.rfilename.split("/")[0] for f in dataset_info.siblings if f.rfilename.endswith(".parquet")}
    # assert that both configs are pushed (push of second config didn't delete first config's files)
    assert len(orig_repo_configs) == 2
    assert orig_repo_configs == original_configs
    # then change the set of dataset configs (remove "second")
    upsert_response(
        kind="dataset-config-names",
        dataset=hub_public_legacy_configs,
        http_status=HTTPStatus.OK,
        content={
            "config_names": [
                {"dataset": hub_public_legacy_configs, "config": "first"},
            ],
        },
    )
    job_runner = get_job_runner(dataset_name, "first", app_config)
    assert job_runner.compute()
    dataset_info = hf_api.dataset_info(
        repo_id=hub_public_legacy_configs, revision=app_config.parquet_and_info.target_revision, files_metadata=False
    )
    updated_repo_files = {f.rfilename for f in dataset_info.siblings}
    # assert that legacy config is removed from the repo
    # and there are only files for config that was just pushed and .gitattributes
    assert ".gitattributes" in updated_repo_files
    assert all(fnmatch(file, "first/*") for file in updated_repo_files.difference({".gitattributes"}))
    updated_repo_configs = {
        f.rfilename.split("/")[0] for f in dataset_info.siblings if f.rfilename.endswith(".parquet")
    }
    assert len(updated_repo_configs) == 1
    assert updated_repo_configs == {"first"}


@pytest.mark.parametrize(
    "dataset,blocked,raises",
    [
        ("public", ["public"], True),
        ("public", ["public", "audio"], True),
        ("public", ["audio"], False),
        ("public", [], False),
    ],
)
def test_raise_if_blocked(dataset: str, blocked: List[str], raises: bool) -> None:
    if raises:
        with pytest.raises(DatasetInBlockListError):
            raise_if_blocked(dataset=dataset, blocked_datasets=blocked)
    else:
        raise_if_blocked(dataset=dataset, blocked_datasets=blocked)


def test_raise_if_requires_manual_download(hub_public_manual_download: str, app_config: AppConfig) -> None:
    builder = load_dataset_builder(hub_public_manual_download)
    with pytest.raises(DatasetManualDownloadError):
        raise_if_requires_manual_download(
            builder=builder,
            hf_endpoint=app_config.common.hf_endpoint,
            hf_token=app_config.common.hf_token,
        )


@pytest.mark.parametrize(
    "name,raises",
    [("public", False), ("big", True)],
)
def test_raise_if_too_big_from_hub(
    hub_public_csv: str,
    hub_public_big: str,
    name: str,
    raises: bool,
    app_config: AppConfig,
) -> None:
    dataset = hub_public_csv if name == "public" else hub_public_big
    dataset_info = get_dataset_info_for_supported_datasets(
        dataset=dataset,
        hf_endpoint=app_config.common.hf_endpoint,
        hf_token=app_config.common.hf_token,
        revision="main",
        files_metadata=True,
    )
    if raises:
        with pytest.raises(DatasetTooBigFromHubError):
            raise_if_too_big_from_hub(
                dataset_info=dataset_info, max_dataset_size=app_config.parquet_and_info.max_dataset_size
            )
    else:
        raise_if_too_big_from_hub(
            dataset_info=dataset_info, max_dataset_size=app_config.parquet_and_info.max_dataset_size
        )


@pytest.mark.parametrize(
    "name,raises",
    [("public", False), ("big", True)],
)
def test_raise_if_too_big_from_datasets(
    hub_public_csv: str,
    hub_public_big: str,
    name: str,
    raises: bool,
    app_config: AppConfig,
) -> None:
    dataset = hub_public_csv if name == "public" else hub_public_big
    builder = load_dataset_builder(dataset)
    if raises:
        with pytest.raises(DatasetTooBigFromDatasetsError):
            raise_if_too_big_from_datasets(
                info=builder.info,
                max_dataset_size=app_config.parquet_and_info.max_dataset_size,
            )
    else:
        raise_if_too_big_from_datasets(
            info=builder.info,
            max_dataset_size=app_config.parquet_and_info.max_dataset_size,
        )


@pytest.mark.parametrize(
    "max_dataset_size,max_external_data_files,raises",
    [
        (None, None, False),
        (10, None, True),
    ],
)
def test_raise_if_too_big_external_files(
    external_files_dataset_builder: "datasets.builder.DatasetBuilder",
    raises: bool,
    max_dataset_size: Optional[int],
    max_external_data_files: Optional[int],
    app_config: AppConfig,
) -> None:
    max_dataset_size = max_dataset_size or app_config.parquet_and_info.max_dataset_size
    max_external_data_files = max_external_data_files or app_config.parquet_and_info.max_external_data_files
    if raises:
        with pytest.raises(DatasetWithTooBigExternalFilesError):
            raise_if_too_big_from_external_data_files(
                builder=external_files_dataset_builder,
                hf_token=app_config.common.hf_token,
                max_dataset_size=max_dataset_size,
                max_external_data_files=max_external_data_files,
            )
    else:
        raise_if_too_big_from_external_data_files(
            builder=external_files_dataset_builder,
            hf_token=app_config.common.hf_token,
            max_dataset_size=max_dataset_size,
            max_external_data_files=max_external_data_files,
        )


@pytest.mark.parametrize(
    "max_dataset_size,max_external_data_files,raises",
    [
        (None, None, False),
        (None, 1, True),
    ],
)
def test_raise_if_too_many_external_files(
    external_files_dataset_builder: "datasets.builder.DatasetBuilder",
    raises: bool,
    max_dataset_size: Optional[int],
    max_external_data_files: Optional[int],
    app_config: AppConfig,
) -> None:
    max_dataset_size = max_dataset_size or app_config.parquet_and_info.max_dataset_size
    max_external_data_files = max_external_data_files or app_config.parquet_and_info.max_external_data_files
    if raises:
        with pytest.raises(DatasetWithTooManyExternalFilesError):
            raise_if_too_big_from_external_data_files(
                builder=external_files_dataset_builder,
                hf_token=app_config.common.hf_token,
                max_dataset_size=max_dataset_size,
                max_external_data_files=max_external_data_files,
            )
    else:
        raise_if_too_big_from_external_data_files(
            builder=external_files_dataset_builder,
            hf_token=app_config.common.hf_token,
            max_dataset_size=max_dataset_size,
            max_external_data_files=max_external_data_files,
        )


def test_supported_if_big_parquet(
    app_config: AppConfig,
    get_job_runner: GetJobRunner,
    hub_reponses_big: HubDatasetTest,
) -> None:
    # Not in the list of supported datasets and bigger than the maximum size
    # but still supported since it's made of parquet files
    # dataset = hub_public_big
    dataset = hub_reponses_big["name"]
    config = hub_reponses_big["config_names_response"]["config_names"][0]["config"]
    upsert_response(
        kind="dataset-config-names",
        dataset=dataset,
        http_status=HTTPStatus.OK,
        content=hub_reponses_big["config_names_response"],
    )
    job_runner = get_job_runner(dataset, config, app_config)
    response = job_runner.compute()
    assert response
    content = response.content
    assert content
    assert len(content["parquet_files"]) == 1
    assert_content_is_equal(content, hub_reponses_big["parquet_and_info_response"])


def test_not_supported_if_big_non_parquet(
    app_config: AppConfig,
    get_job_runner: GetJobRunner,
    hub_reponses_big_csv: HubDatasetTest,
) -> None:
    # Not in the list of supported datasets and bigger than the maximum size
    # dataset = hub_public_big_csv
    dataset = hub_reponses_big_csv["name"]
    config = hub_reponses_big_csv["config_names_response"]["config_names"][0]["config"]
    upsert_response(
        kind="dataset-config-names",
        dataset=dataset,
        http_status=HTTPStatus.OK,
        content=hub_reponses_big_csv["config_names_response"],
    )
    job_runner = get_job_runner(dataset, config, app_config)
    with pytest.raises(CustomError) as e:
        job_runner.compute()
    assert e.typename == "DatasetTooBigFromHubError"


def test_supported_if_gated(
    app_config: AppConfig,
    get_job_runner: GetJobRunner,
    hub_reponses_gated: HubDatasetTest,
) -> None:
    # Access must be granted
    dataset = hub_reponses_gated["name"]
    config = hub_reponses_gated["config_names_response"]["config_names"][0]["config"]
    upsert_response(
        "dataset-config-names",
        dataset=dataset,
        http_status=HTTPStatus.OK,
        content=hub_reponses_gated["config_names_response"],
    )
    job_runner = get_job_runner(dataset, config, app_config)
    response = job_runner.compute()
    assert response
    assert response.content


def test_blocked(
    app_config: AppConfig,
    get_job_runner: GetJobRunner,
    hub_reponses_jsonl: HubDatasetTest,
) -> None:
    # In the list of blocked datasets
    with blocked(app_config, repo_id=hub_reponses_jsonl["name"]):
        dataset = hub_reponses_jsonl["name"]
        config = hub_reponses_jsonl["config_names_response"]["config_names"][0]["config"]
        upsert_response(
            kind="dataset-config-names",
            dataset=dataset,
            http_status=HTTPStatus.OK,
            content=hub_reponses_jsonl["config_names_response"],
        )
        job_runner = get_job_runner(dataset, config, app_config)
        with pytest.raises(CustomError) as e:
            job_runner.compute()
        assert e.typename == "DatasetInBlockListError"


@pytest.mark.parametrize(
    "name",
    ["public", "audio", "gated"],
)
def test_compute_splits_response_simple_csv_ok(
    hub_reponses_public: HubDatasetTest,
    hub_reponses_audio: HubDatasetTest,
    hub_reponses_gated: HubDatasetTest,
    get_job_runner: GetJobRunner,
    name: str,
    app_config: AppConfig,
    data_df: pd.DataFrame,
) -> None:
    hub_datasets = {"public": hub_reponses_public, "audio": hub_reponses_audio, "gated": hub_reponses_gated}
    dataset = hub_datasets[name]["name"]
    config = hub_datasets[name]["config_names_response"]["config_names"][0]["config"]
    upsert_response(
        "dataset-config-names",
        dataset=dataset,
        http_status=HTTPStatus.OK,
        content=hub_datasets[name]["config_names_response"],
    )
    expected_parquet_and_info_response = hub_datasets[name]["parquet_and_info_response"]
    job_runner = get_job_runner(dataset, config, app_config)
    result = job_runner.compute().content
    assert_content_is_equal(result, expected_parquet_and_info_response)

    # download the parquet file and check that it is valid
    if name == "audio":
        return

    if name == "public":
        df = pd.read_parquet(result["parquet_files"][0]["url"], engine="auto")
    else:
        # in all these cases, the parquet files are not accessible without a token
        with pytest.raises(Exception):
            pd.read_parquet(result["parquet_files"][0]["url"], engine="auto")
        r = requests.get(
            result["parquet_files"][0]["url"], headers={"Authorization": f"Bearer {app_config.common.hf_token}"}
        )
        assert r.status_code == HTTPStatus.OK, r.text
        df = pd.read_parquet(io.BytesIO(r.content), engine="auto")
    assert df.equals(data_df), df


@pytest.mark.parametrize(
    "name,error_code,cause",
    [
        ("private", "DatasetNotFoundError", None),
    ],
)
def test_compute_splits_response_simple_csv_error(
    hub_reponses_private: HubDatasetTest,
    get_job_runner: GetJobRunner,
    name: str,
    error_code: str,
    cause: str,
    app_config: AppConfig,
) -> None:
    dataset = hub_reponses_private["name"]
    config_names_response = hub_reponses_private["config_names_response"]
    config = config_names_response["config_names"][0]["config"] if config_names_response else None
    upsert_response(
        "dataset-config-names",
        dataset=dataset,
        http_status=HTTPStatus.OK,
        content=hub_reponses_private["config_names_response"],
    )
    job_runner = get_job_runner(dataset, config, app_config)
    with pytest.raises(CustomError) as exc_info:
        job_runner.compute()
    assert exc_info.value.code == error_code
    assert exc_info.value.cause_exception == cause
    if exc_info.value.disclose_cause:
        response = exc_info.value.as_response()
        assert set(response.keys()) == {"error", "cause_exception", "cause_message", "cause_traceback"}
        response_dict = dict(response)
        # ^ to remove mypy warnings
        assert response_dict["cause_exception"] == cause
        assert isinstance(response_dict["cause_traceback"], list)
        assert response_dict["cause_traceback"][0] == "Traceback (most recent call last):\n"


@pytest.mark.parametrize(
    "name,error_code,cause",
    [
        ("public", "CachedResponseNotFound", None),  # no cache for dataset-config-names -> CachedResponseNotFound
    ],
)
def test_compute_splits_response_simple_csv_error_2(
    hub_reponses_public: HubDatasetTest,
    get_job_runner: GetJobRunner,
    name: str,
    error_code: str,
    cause: str,
    app_config: AppConfig,
) -> None:
    dataset = hub_reponses_public["name"]
    config_names_response = hub_reponses_public["config_names_response"]
    config = config_names_response["config_names"][0]["config"] if config_names_response else None
    job_runner = get_job_runner(dataset, config, app_config)
    with pytest.raises(CachedArtifactError):
        job_runner.compute()


@pytest.mark.parametrize(
    "upstream_status,upstream_content,exception_name",
    [
        (HTTPStatus.NOT_FOUND, {"error": "error"}, "CachedArtifactError"),
        (HTTPStatus.OK, {"not_config_names": "wrong_format"}, "PreviousStepFormatError"),
        (HTTPStatus.OK, {"config_names": "not a list"}, "PreviousStepFormatError"),
    ],
)
def test_previous_step_error(
    get_job_runner: GetJobRunner,
    upstream_status: HTTPStatus,
    upstream_content: Any,
    exception_name: str,
    hub_reponses_public: HubDatasetTest,
    app_config: AppConfig,
) -> None:
    dataset = hub_reponses_public["name"]
    config = hub_reponses_public["config_names_response"]["config_names"][0]["config"]
    job_runner = get_job_runner(dataset, config, app_config)
    upsert_response(
        "dataset-config-names",
        dataset=dataset,
        http_status=upstream_status,
        content=upstream_content,
    )
    with pytest.raises(Exception) as exc_info:
        job_runner.compute()
    assert exc_info.typename == exception_name


@pytest.mark.parametrize(
    "filename,split,config,raises",
    [
        ("config/builder-split.parquet", "split", "config", False),
        ("config/builder-with-dashes-split.parquet", "split", "config", False),
        ("config/builder-split-00000-of-00001.parquet", "split", "config", False),
        ("config/builder-with-dashes-split-00000-of-00001.parquet", "split", "config", False),
        ("config/builder-split.with.dots-00000-of-00001.parquet", "split.with.dots", "config", False),
        (
            "config/builder-with-dashes-caveat-asplitwithdashesisnotsupported-00000-of-00001.parquet",
            "asplitwithdashesisnotsupported",
            "config",
            False,
        ),
        ("builder-split-00000-of-00001.parquet", "split", "config", True),
        ("plain_text/openwebtext-10k-train.parquet", "train", "plain_text", False),
        ("plain_text/openwebtext-10k-train-00000-of-00001.parquet", "train", "plain_text", False),
    ],
)
def test_parse_repo_filename(filename: str, split: str, config: str, raises: bool) -> None:
    if raises:
        with pytest.raises(Exception):
            parse_repo_filename(filename)
    else:
        assert parse_repo_filename(filename) == (config, split)


@pytest.mark.parametrize(
    "ds_info, has_big_chunks",
    [
        (datasets.info.DatasetInfo(), False),
        (datasets.info.DatasetInfo(features=Features({"text": Value("string")})), False),
        (datasets.info.DatasetInfo(features=Features({"image": Image()})), True),
        (datasets.info.DatasetInfo(features=Features({"audio": Audio()})), True),
        (datasets.info.DatasetInfo(features=Features({"nested": [{"image": Image()}]})), True),
        (datasets.info.DatasetInfo(features=Features({"blob": Value("binary")})), True),
    ],
)
def test_get_writer_batch_size(ds_info: datasets.info.DatasetInfo, has_big_chunks: bool) -> None:
    assert get_writer_batch_size(ds_info) == (100 if has_big_chunks else None)
