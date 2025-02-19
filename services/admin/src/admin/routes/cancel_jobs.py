# SPDX-License-Identifier: Apache-2.0
# Copyright 2022 The HuggingFace Authors.

import logging
from typing import Optional

from libcommon.queue import Queue
from starlette.requests import Request
from starlette.responses import Response

from admin.authentication import auth_check
from admin.utils import (
    AdminCustomError,
    Endpoint,
    UnexpectedError,
    get_json_admin_error_response,
    get_json_ok_response,
)


def create_cancel_jobs_endpoint(
    job_type: str,
    external_auth_url: Optional[str] = None,
    organization: Optional[str] = None,
    hf_timeout_seconds: Optional[float] = None,
) -> Endpoint:
    async def cancel_jobs_endpoint(request: Request) -> Response:
        try:
            logging.info(f"/cancel-jobs/{job_type}")

            # if auth_check fails, it will raise an exception that will be caught below
            auth_check(
                external_auth_url=external_auth_url,
                request=request,
                organization=organization,
                hf_timeout_seconds=hf_timeout_seconds,
            )
            Queue().cancel_started_jobs(job_type=job_type)
            return get_json_ok_response(
                {"status": "ok"},
                max_age=0,
            )
        except AdminCustomError as e:
            return get_json_admin_error_response(e, max_age=0)
        except Exception as e:
            return get_json_admin_error_response(UnexpectedError("Unexpected error.", e), max_age=0)

    return cancel_jobs_endpoint
