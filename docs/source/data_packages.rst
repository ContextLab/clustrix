Data packages
=============

Clustrix ships your function and its arguments. It does not ship your dataset.
A function that opens ``"data/subjects.h5"`` finds that file on your laptop and
does not find it on the worker, and no amount of decorating changes that. A
data package is how a dataset travels: you name the files, clustrix moves them,
and the function reads them back through the package on whichever machine it
happens to be running on.

Nothing is inferred. A file moves because you named it, never because a string
in your source code looked like a path. That restraint is deliberate: an upload
triggered by the literal ``"s3://bucket/notes.log"`` is the worst failure mode
available here, so declaration is the only route.

The shape of it
---------------

Three steps, and the middle one is the ordinary one:

.. code-block:: python

   # cluster-required: needs a configured cluster and a real data/ directory
   import clustrix

   # 1. Declare
   subjects = clustrix.data_package("data/subjects.h5")

   # 2. Pass it like any other argument
   @clustrix.cluster(cores=8)
   def fit(pkg):
       # 3. Dereference inside the function
       with open(pkg.path("subjects.h5"), "rb") as handle:
           return len(handle.read())

   fit(subjects)

:func:`clustrix.data_package` accepts a path, a list of paths, a directory, or
raw ``bytes``. A directory expands to the files beneath it, and a list may mix
files and directories freely. Whatever you name, the paths inside the package
are relative to the common ancestor of everything named, so a function written
against ``"data/subjects.h5"`` keeps working against ``"data/subjects.h5"`` on
the worker. Pass ``base=`` when you want a different root.

Dereference with :meth:`~clustrix.DataPackage.path`, which returns a filesystem
path, or with :meth:`~clustrix.DataPackage.read_bytes`, which returns the
contents. Both check the file against the digest recorded when the package was
built, so a file that arrived wrong is an error rather than a wrong answer.
Both take no argument at all when the package holds exactly one file. A package
dereferenced on the machine that built it reads your original files in place --
no copy, no fetch -- provided their digests still match what was packaged.

Here is the same round trip against the ``local`` backend, which needs no
cluster and no network, and which this page executes for real every time it is
checked:

.. code-block:: python

   import os

   import clustrix
   from clustrix import configure

   configure(cluster_type="local")

   os.makedirs("trials", exist_ok=True)
   with open("trials/run1.csv", "w") as handle:
       handle.write("trial,rt\n1,0.42\n")

   trials = clustrix.data_package("trials", force_local=True)
   print(trials.filenames(), trials.total_bytes)   # ['run1.csv'] 16

   @clustrix.cluster(cores=2)
   def count_rows(pkg):
       with open(pkg.path("run1.csv")) as handle:
           return len(handle.readlines())

   print(count_rows(trials))                       # 2

``force_local=True`` says "carry the contents inside the object regardless of
size". Nothing is uploaded, and there is nothing to clean up afterwards.

Several packages travel as easily as one, and a list of them is walked the same
way a single one is:

.. code-block:: python

   # cluster-required: needs a configured cluster to execute
   import os

   import clustrix

   packages = [
       clustrix.data_package("data/subjects.h5"),
       clustrix.data_package("data/stimuli/"),
   ]

   @clustrix.cluster(cores=4)
   def summarize(pkgs):
       roots = clustrix.materialize_packages(pkgs)
       return [len(os.listdir(root)) for root in roots]

   summarize(packages)

:func:`clustrix.materialize_packages` walks lists, tuples and dicts, writes
every package it finds to local disk, and hands back the directory holding
each. Anything that is not a package comes back unchanged.

.. _data-package-where-bytes-live:

Where the bytes actually live
-----------------------------

Two places, and which one you get depends on size.

**Small packages ride inside the object.** Below ``stage_inline_max_bytes``,
the file contents are carried in the package itself, pickled alongside your
function's other arguments, and shipped over the transport that already moves
the payload -- SFTP for ``ssh`` and ``slurm``, the payload channel for
``huggingface``. No second transport, no remote store, nothing to delete.

**Larger packages go to a private HuggingFace dataset repo.** Above that
threshold, the contents are uploaded and the package carries only the
coordinates plus a digest per file. Three things follow, and you want to know
all of them before it happens rather than after:

1. **It needs HuggingFace credentials.** The token comes from ``hf_token`` in
   your config, then ``HF_TOKEN`` in the environment, then the cache that
   ``hf auth login`` writes. Without one, staging refuses and says so. The
   worker needs one too, from its own environment: clustrix deliberately does
   not pickle your token into the job payload, so a package staged remotely is
   unreadable on a worker with no HuggingFace credentials of its own.
2. **Clustrix creates a repo in your account.** The first package that does not
   fit inline calls ``create_repo(private=True, exist_ok=True)`` for
   ``<namespace>/clustrix-data``. The namespace is ``hf_namespace`` if you set
   one, otherwise ``hf_username``, otherwise whatever the token's ``whoami()``
   reports. Set ``hf_data_repo`` to name a different repo outright.
3. **This applies on every backend.** A ``slurm`` job with a package too big to
   inline still stages that package through HuggingFace, because that is the
   only remote store clustrix has.

Each package gets its own folder in that repo, keyed by a fresh identifier, so
two packages never share a stored blob and deleting one cannot pull data out
from under another. The cost of that is duplication: identical content packaged
twice is stored twice.

Digests are computed locally, from your own files, and travel to the worker
inside the function payload -- which is an upload-only, local-origin artifact.
Bytes fetched back out of the store are checked against those digests. In this
way a tampered store is caught, because the expected digest never went through
it.

The three size thresholds
-------------------------

.. list-table::
   :header-rows: 1
   :widths: 30 18 52

   * - Field
     - Default
     - Effect
   * - ``stage_inline_max_bytes``
     - 1 MB
     - Under this, the package is inline. At or above it, the package is
       uploaded.
   * - ``stage_warn_bytes``
     - 100 MB
     - At or above this, staging logs a warning before it starts. A transfer
       that takes minutes with no output is indistinguishable from a hang.
   * - ``stage_max_bytes``
     - 5 GB
     - At or above this, staging raises :class:`clustrix.StagingError` and
       names the largest file. Raise it if you genuinely mean to move that
       much over the network.

The inline threshold is measured on the **serialized package**, not on the raw
data. Those two numbers are nowhere near each other once a package holds many
small files, because every file also carries a relative path and a 64-character
digest: ten thousand four-byte files are forty kilobytes of data and a 1.09 MB
pickle. Measured on the data alone, that package would be a megabyte over the
limit and still call itself inline, which is why the limit is applied to the
pickle instead.

Deleting a package
------------------

**Nothing is ever cleaned up for you.** There is no TTL, no reaper, no eviction
policy, and no deletion when a job finishes. ``cleanup_on_success`` governs the
job directory on the cluster and does not touch staged data. Whether a dataset
is still needed is your judgement rather than clustrix's, so a package you
staged stays staged, and stays billable, until you say otherwise.

The handle that created a package can end it:

.. code-block:: python

   # cluster-required: needs HuggingFace credentials
   import clustrix

   pkg = clustrix.data_package("data/subjects.h5")
   pkg.delete()

:meth:`~clustrix.DataPackage.delete` returns whether anything was actually
removed. Calling it twice is not an error, and neither is calling it on a
package somebody already cleaned up elsewhere. A remote copy that is present
and refuses to delete *does* raise, because a warning there would leave you
paying for storage you believe you released.

Four things it does not touch, each for its own reason:

- **The files you packaged.** ``local_root`` points at your own data.
- **A directory you named yourself.** If you called ``materialize(dest=...)``,
  that directory is yours; it may hold anything, and clustrix cannot tell what
  it put there from what was already there.
- **The cache directory above the package.** Exactly one directory is removed,
  ``<local_cache_dir>/data-packages/<package id>``, which clustrix created and
  which is keyed by an identifier nothing else uses.
- **The repo itself**, only the package's folder inside it. An account whose
  last package is deleted keeps an empty ``clustrix-data`` dataset, which is
  yours to remove by hand. A user who pointed ``hf_data_repo`` at a repo they
  own and care about would not thank clustrix for deleting it because the last
  package went away.

There is deliberately no context-manager form. A ``with`` block that quietly
deleted the upload on the way out would be exactly the automatic cleanup this
design rejects.

When the object is gone
~~~~~~~~~~~~~~~~~~~~~~~

Losing the handle does not mean losing the ability to clean up, because the
object is not the only key to its own deletion:

.. code-block:: python

   # cluster-required: needs HuggingFace credentials
   import clustrix

   for record in clustrix.list_data_packages():
       print(record["package_id"], record["name"], record["total_bytes"])

   clustrix.delete_data_package("0123456789abcdef0123456789abcdef")

:func:`clustrix.list_data_packages` returns one record per package in the
store; an upload that was interrupted before its manifest went up appears with
``"complete": False``. :func:`clustrix.delete_data_package` takes an id and
validates it before anything reaches the Hub, since the id becomes a path in
the store and an id that is not one is a deletion aimed somewhere else.

Keeping a package across sessions
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

The package object is the durable handle, and it is plain data: strings, ints,
bytes. No client, no socket, no credential. So the way to keep a staged dataset
across sessions is to pickle the object and load it later, and a copy loaded in
a fresh interpreter still reaches the same remote data:

.. code-block:: python

   # cluster-required: needs HuggingFace credentials
   import pickle

   import clustrix

   pkg = clustrix.data_package("data/subjects.h5")
   with open("subjects.pkl", "wb") as handle:
       pickle.dump(pkg, handle)

   # ... a week later, a different interpreter ...
   with open("subjects.pkl", "rb") as handle:
       pkg = pickle.load(handle)

   pkg.path("subjects.h5")   # still resolves
   pkg.delete()              # still deletes

Credentials are deliberately not among the attributes that survive that round
trip. A saved package must not be a token sitting on disk, so it re-authenticates
from the ordinary config path every time it is used.

What it refuses
---------------

Every refusal below raises :class:`clustrix.StagingError` before anything
moves.

**Paths that look like credentials.** ``*.pem``, ``*.key``, ``.env``,
``.netrc``, ``id_rsa*``, ``kubeconfig``, anything under ``.ssh/``, ``.aws/``,
``.gnupg/``, ``.kube/`` or ``.config/gcloud/``, a ``.git/config`` (which is one
of the commonest places a personal access token ends up on disk), and so on.
Both ends of a symlink are tested, since ``data/notes`` pointing at
``~/.ssh/id_rsa`` is credential-shaped at the far end and innocuous at the
near one. Pass ``allow_sensitive=True`` if you really do mean to move a keypair
to the worker.

**Anything that is not a regular file.** Reading a fifo or ``/dev/zero`` does
not fail, it *blocks*, so packaging one would hang with no output and no
timeout. A refusal costs you one message.

**A data repo that already exists and is public.** ``create_repo(private=True,
exist_ok=True)`` creates a private repo but does not make an existing public
one private; it returns the repo as it is. Clustrix refuses rather than
flipping the setting, because a repo can be public on purpose and silently
changing someone's visibility is its own incident. Make it private yourself, or
point ``hf_data_repo`` somewhere else.

**A package at or above ``stage_max_bytes``**, with the largest file named.

**Two different files that would land on the same name.** One would silently
overwrite the other on the worker and the run would produce a wrong answer
rather than an error. Naming the same file twice is harmless and collapses.

**Files that change while they are being staged.** Files go up by path, so the
bytes on the wire are whatever the file held at upload time rather than what
was hashed a moment earlier. Clustrix re-hashes after the commit and, if
anything moved, removes the folder it just created and tells you which files
changed.

When not to use this
--------------------

If the data is already reachable from the worker, staging it is pure waste. On
an HPC cluster with a shared filesystem your ``/scratch`` directory is visible
from every compute node, so the file your function opens is already there and
moving a copy of it through HuggingFace buys nothing but a transfer and a
storage bill. The same goes for data in object storage the worker can
authenticate to.

Use the read-only :doc:`filesystem utilities <api/filesystem>` to check.
``cluster_exists`` answers the question directly:

.. code-block:: python

   from clustrix import cluster_exists
   from clustrix.config import ClusterConfig

   config = ClusterConfig(cluster_type="local", local_work_dir=".")

   if cluster_exists("setup.py", config):
       print("already there; nothing to stage")

Data packages are for the case where that answer is no.

API
---

.. currentmodule:: clustrix.staging

.. autofunction:: clustrix.data_package

.. autoclass:: clustrix.DataPackage
   :no-undoc-members:
   :members: path, read_bytes, materialize, delete, exists, filenames,
             is_inline, total_bytes

.. autofunction:: clustrix.materialize_packages

.. autofunction:: clustrix.list_data_packages

.. autofunction:: clustrix.delete_data_package

.. autoexception:: clustrix.StagingError
