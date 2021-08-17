#!/usr/bin/env python3
# Moldavite
# Copyright(C) 2021 The Moldavite Authors.
#
# This program is free software: you can redistribute it and / or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program. If not, see <http://www.gnu.org/licenses/>.

"""Implementation of API v1."""

import os
import logging
from typing import Any
from typing import Dict
from typing import Tuple

import datetime
from dateutil.parser import parse as datetime_parser
from thoth.common.exceptions import NotFoundExceptionError
from thoth.common import OpenShift

from .configuration import Configuration

_LOGGER = logging.getLogger(__name__)

_OPENSHIFT = OpenShift()

_WORKFLOW_TEMPLATE_LABEL_SELECTOR = os.getenv(
    "THOTH_MOLDAVITE_TEMPLATE_LABEL_SELECTOR", "template=moldavite-jupyterbook-build"
)
_DEFAULT_BOOK_PATH = os.getenv("THOTH_MOLDAVITE_DEFAULT_BOOK_PATH", "book")
_DEFAULT_GIT_BRANCH = os.getenv("THOTH_MOLDAVITE_DEFAULT_GIT_BRANCH", "main")
_MAX_TTL = int(
    os.getenv("THOTH_MOLDAVITE_MAX_TTL", datetime.timedelta(days=3).total_seconds())
)
_DEFAULT_TTL = int(
    os.getenv("THOTH_MOLDAVITE_DEFAULT_TTL", datetime.timedelta(days=1).total_seconds())
)
_RESOURCES_CREATED = frozenset(
    {
        ("apps.openshift.io/v1", "DeploymentConfig"),
        ("argoproj.io/v1alpha1", "Workflow"),
        ("image.openshift.io/v1", "ImageStream"),
        ("route.openshift.io/v1", "Route"),
        ("v1", "Service"),
    }
)


def _get_openshift_resource(
    openshift: OpenShift,
    api_version: str,
    kind: str,
    label_selector: str,
    namespace: str,
) -> Dict[str, Any]:
    """Get a single pod name from a job."""
    # Kubernetes automatically adds 'job-name' label -> reuse it.
    response = openshift.ocp_client.resources.get(
        api_version=api_version, kind=kind
    ).get(
        namespace=namespace,
        label_selector=label_selector,
    )
    response = response.to_dict()
    if len(response["items"]) != 1:
        if len(response["items"]) > 1:
            # Log this error and report back to user not found.
            _LOGGER.error(
                "Multiple resources of kind %r (API version %r) for label selector %r found in %r",
                kind,
                api_version,
                label_selector,
                namespace,
            )

        raise NotFoundExceptionError(
            f"Multiple resources of kind {kind!r} (API version {api_version!r}) "
            f"for label selector {label_selector!r} found in {namespace!r}",
        )

    ret: Dict[str, Any] = response["items"][0]
    return ret


def get_version() -> Dict[str, Any]:
    """Obtain version identifier."""
    from moldavite import __version__
    from moldavite.entrypoint import __service_version__

    return {
        "version": __version__,
        "service_version": __service_version__,
    }


def post_jupyterbook_build(specification: Dict[str, Any]) -> Tuple[Dict[str, str], int]:
    """Create a JupyterBook build request."""
    repo_url = specification["repo_url"].strip()
    book_path = specification.get("book_path", _DEFAULT_BOOK_PATH)
    git_branch = specification.get("git_branch", _DEFAULT_GIT_BRANCH)
    ttl = int(specification.get("ttl", _DEFAULT_TTL))

    if ttl > _MAX_TTL:
        return {"error": f"TTL exceeded, maximum TTL can be {_MAX_TTL}"}, 400

    book_id = _OPENSHIFT.generate_id("book")

    _OPENSHIFT.workflow_manager.submit_workflow_from_template(
        namespace=Configuration.INFRA_NAMESPACE,
        label_selector=_WORKFLOW_TEMPLATE_LABEL_SELECTOR,
        template_parameters={
            "MOLDAVITE_REPO_URL": repo_url,
            "MOLDAVITE_BOOK_ID": book_id,
            "MOLDAVITE_REPO_BRANCH": git_branch,
            "MOLDAVITE_BOOK_PATH": book_path,
            "MOLDAVITE_TTL": ttl,
        },
        workflow_parameters={},
        workflow_namespace=Configuration.BUILD_NAMESPACE,
    )

    return (
        {
            "book_id": book_id,
            "repo_url": repo_url,
            "git_branch": git_branch,
            "book_path": book_path,
        },
        202,
    )


def get_jupyterbook_status(book_id: str) -> Tuple[Dict[str, Any], int]:
    """Get status of the given JupyterBook."""
    # Workflow information.
    build_status = None
    try:
        build_status = _OPENSHIFT.get_workflow_status_report(
            workflow_id=book_id, namespace=Configuration.BUILD_NAMESPACE
        )
    except NotFoundExceptionError:
        _LOGGER.warning(
            f"No workflow {book_id!r} found in {Configuration.BUILD_NAMESPACE}"
        )

    # Information from Route.
    host = None
    try:
        route = _get_openshift_resource(
            _OPENSHIFT,
            api_version="route.openshift.io/v1",
            kind="Route",
            label_selector=f"build_id={book_id}",
            namespace=Configuration.BUILD_NAMESPACE,
        )
    except NotFoundExceptionError:
        _LOGGER.warning(
            f"No route {book_id!r} found in {Configuration.BUILD_NAMESPACE}"
        )
    else:
        host = route["status"]["ingress"][0]["host"]

    # Information from DeploymentConfig
    git_branch, book_path, repo_url, ttl = None, None, None, None
    try:
        deployment_config = _get_openshift_resource(
            _OPENSHIFT,
            api_version="apps.openshift.io/v1",
            kind="DeploymentConfig",
            label_selector=f"build_id={book_id}",
            namespace=Configuration.BUILD_NAMESPACE,
        )
    except NotFoundExceptionError:
        _LOGGER.warning(
            f"No DeploymentConfig {book_id!r} found in {Configuration.BUILD_NAMESPACE}"
        )
    else:
        git_branch = deployment_config["spec"]["template"]["metadata"]["annotations"][
            "moldavite.repo_branch"
        ]
        book_path = deployment_config["spec"]["template"]["metadata"]["annotations"][
            "moldavite.book_path"
        ]
        repo_url = deployment_config["spec"]["template"]["metadata"]["annotations"][
            "moldavite.repo_url"
        ]

        ttl_configured = int(
            deployment_config["metadata"]["labels"]["ttl"][:-1]
        )  # strip [s]
        creation = datetime_parser(deployment_config["metadata"]["creationTimestamp"])
        ttl = max(
            int(
                (
                    (creation + datetime.timedelta(seconds=ttl_configured))
                    - datetime.datetime.now(datetime.timezone.utc)
                ).total_seconds()
            ),
            0,
        )

    if build_status is None and ttl is None:
        return (
            {
                "error": f"Book {book_id!r} does not exist or the build has not started yet"
            },
            404,
        )

    return (
        {
            "host": host,
            "ttl": ttl,
            "book_id": book_id,
            "book_path": book_path,
            "git_branch": git_branch,
            "repo_url": repo_url,
            "build_status": build_status,
        },
        200,
    )


def delete_jupyterbook(book_id: str) -> Tuple[Dict[str, str], int]:
    """Delete the given JupyterBook."""
    any_found = False
    for api_version, kind in _RESOURCES_CREATED:
        response = _OPENSHIFT.ocp_client.resources.get(
            api_version=api_version,
            kind=kind,
        ).delete(
            namespace=Configuration.BUILD_NAMESPACE,
            label_selector=f"build_id={book_id}",
        )
        response = response.to_dict()
        if len(response["items"]) != 0:
            any_found = True

    if not any_found:
        return (
            {
                "error": f"Book {book_id!r} does not exist or the build has not started yet"
            },
            404,
        )

    return {"book_id": book_id}, 201


def post_jupyterhub_build(specification: Dict[str, Any]) -> Tuple[Dict[str, str], int]:
    """Create a JupyterHub build request."""
    repo_url = specification["repo_url"].strip()
    git_branch = specification.get("git_branch", _DEFAULT_GIT_BRANCH)
    notebook_id = _OPENSHIFT.generate_id("notebook")

    _OPENSHIFT.workflow_manager.submit_workflow_from_template(
        namespace=Configuration.INFRA_NAMESPACE,
        label_selector="template=moldavite-jupyterhub-notebook-build",
        template_parameters={
            "MOLDAVITE_REPO_URL": repo_url,
            "MOLDAVITE_NOTEBOOK_ID": notebook_id,
            "MOLDAVITE_REPO_BRANCH": git_branch,
        },
        workflow_parameters=_OPENSHIFT._assign_workflow_parameters_for_ceph(),  # XXX: remove private call
        workflow_namespace=Configuration.BUILD_NAMESPACE,
    )

    return (
        {"notebook_id": notebook_id, "repo_url": repo_url, "git_branch": git_branch},
        202,
    )


def get_jupyterhub_status(notebook_id: str) -> Tuple[Dict[str, Any], int]:
    """Get status of the given JupyterHub NoteBook."""
    # Workflow information.
    build_status = None
    try:
        build_status = _OPENSHIFT.get_workflow_status_report(
            workflow_id=notebook_id, namespace=Configuration.BUILD_NAMESPACE
        )
    except NotFoundExceptionError:
        _LOGGER.warning(
            f"No workflow {notebook_id!r} found in {Configuration.BUILD_NAMESPACE}"
        )

    if build_status is None:
        return (
            {
                "error": f"NoteBook {notebook_id!r} does not exist or the build has not started yet"
            },
            404,
        )

    return (
        {
            "notebook_id": notebook_id,
            "build_status": build_status,
        },
        200,
    )


def delete_jupyterhub(notebook_id: str) -> Tuple[Dict[str, str], int]:
    """Delete the given JupyterHub NoteBook."""
    any_found = False
    for api_version, kind in _RESOURCES_CREATED:
        response = _OPENSHIFT.ocp_client.resources.get(
            api_version=api_version,
            kind=kind,
        ).delete(
            namespace=Configuration.BUILD_NAMESPACE,
            label_selector=f"build_id={notebook_id}",
        )
        response = response.to_dict()
        if len(response["items"]) != 0:
            any_found = True

    if not any_found:
        return (
            {
                "error": f"NoteBook {notebook_id!r} does not exist or the build has not started yet"
            },
            404,
        )

    return {"notebook_id": notebook_id}, 201
