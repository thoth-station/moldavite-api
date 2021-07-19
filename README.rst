💎 Moldavite
------------

.. note::

  Moldavite is a forest green, olive green or blue greenish vitreous silica
  projectile rock formed by a meteorite impact probably in southern Germany.

Source: `Wikipedia <https://en.wikipedia.org/wiki/Moldavite>`__

Application
===========

This application creates functionality similar to `mybinder.org
<https://mybinder.org/>`__ service. A user supplies Git URL repository, path to
`JupyterBook <https://jupyterbook.org/>`__ and other information (such as Git
branch, lifetime of the deployed JupyterBook instance, ...) and the service
builds a JupyterBook that is hosted on OpenShift for the specified lifetime.

Requirements for deployment
===========================

* OpenShift cluster
* Argo Workflows
* Thoth's `cleanup-job <https://github.com/thoth-station/cleanup-job>`__ - used for
  cleaning resources in the cluster
* privileged containers for running ``podman``/``buildah`` to create JupyterBook


See `example repo for example input <https://github.com/fridex/moldavite-example>`__.

Running the application locally
===============================

The API server can be run locally without being deployed to the cluster. You
need to log in using ``oc`` and be active in the desired namespace. The API
server detects that it is running locally and talks to the Kubernetes API under
the hood, as if the API server would be run in a cluster:

.. code:: console

  pipenv run gunicorn moldavite.entrypoint:app

Check ``.env.template`` for setting up required environment variables and
`thoth-common <https://github.com/thoth-station/common>`__ for logging setup.
