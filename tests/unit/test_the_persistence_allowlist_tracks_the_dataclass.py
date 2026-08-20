#!/usr/bin/env python3
"""What may be written to disk is derived from ``ClusterConfig``, not listed.

Three separate rules decide what ``strip_secret_fields`` lets through, and
all three are *derived* from the dataclass rather than written out by hand.
That is the whole design, and nothing was checking it, so each one could
quietly stop tracking the dataclass:

* ``PERSISTABLE_KEYS`` -- replacing it with a static literal and then adding
  two fields to ``ClusterConfig`` was **not detected by any test**. It fails
  closed, so it is a durability bug rather than a leak, but a user who adds
  a configuration field and finds it silently not saved has been lied to.
* ``UNCLASSIFIABLE_FIELDS`` -- was the literal ``{"environment_variables"}``
  while the identical argument applied word for word to ``gpu_requirements``
  and ``venv_info``, which were **not** in it. ``strip_secret_fields`` does
  not descend, so ``gpu_requirements={"api_key": ...}`` reached disk
  verbatim.
* ``SECRET_FIELDS`` -- derived from the field names by pattern, with two
  exemptions that are sound only over the dataclass's own names.

No mocks: every assertion is against the real declarations and, where it
matters, against a real file written and read back.
"""

import collections.abc
import dataclasses
from dataclasses import fields
from typing import Any, Dict, List, Mapping, MutableMapping, Optional, Union

import pytest

from clustrix.config import (
    CONFIG_FILE_METADATA_KEYS,
    DECLARED_FIELD_NAMES,
    PERSISTABLE_KEYS,
    SECRET_FIELDS,
    UNCLASSIFIABLE_FIELDS,
    ClusterConfig,
    _is_opaque_mapping,
    _is_secret_field,
    strip_secret_fields,
)

FIELD_NAMES = frozenset(f.name for f in fields(ClusterConfig))


def test_persistable_keys_is_the_dataclass_plus_the_file_format_extras():
    """The mutant M9 killer: a static literal cannot follow the dataclass.

    Stated as an equality rather than a subset in each direction, because
    both failures matter: a name the allowlist has and the dataclass does
    not is a key nothing can read back, and a field the dataclass has and
    the allowlist does not is a setting that silently does not persist.
    """
    assert PERSISTABLE_KEYS == FIELD_NAMES | CONFIG_FILE_METADATA_KEYS


def test_every_declared_field_is_persistable_unless_it_was_withheld_on_purpose():
    """There are exactly two reasons for a field not to reach disk."""
    withheld = {name for name in FIELD_NAMES if name not in PERSISTABLE_KEYS}
    assert withheld == set(), sorted(withheld)

    dropped = {
        name
        for name in FIELD_NAMES
        if name not in strip_secret_fields({name: "x" for name in FIELD_NAMES})
    }
    assert dropped == SECRET_FIELDS | UNCLASSIFIABLE_FIELDS, sorted(dropped)


def test_a_plain_setting_really_survives_a_save_and_a_load(tmp_path):
    """The behavioural half: the allowlist is not just internally consistent.

    A field can be in ``PERSISTABLE_KEYS`` and still be lost on the way
    back, so this writes a real file with the shipped writer and reads it
    back with the shipped reader.
    """
    path = tmp_path / "config.yml"
    ClusterConfig(
        cluster_type="ssh",
        cluster_host="cluster.example.edu",
        username="researcher",
        default_cores=17,
        remote_work_dir="/scratch/researcher",
        module_loads=["python/3.11"],
    ).save_to_file(str(path))

    restored = ClusterConfig.load_from_file(str(path))

    assert restored.cluster_host == "cluster.example.edu"
    assert restored.default_cores == 17
    assert restored.remote_work_dir == "/scratch/researcher"
    assert restored.module_loads == ["python/3.11"]


# --------------------------------------------------------------------------
# The opaque mappings.
# --------------------------------------------------------------------------


def test_every_mapping_field_is_withheld():
    """A ``Dict`` field is a hole in any name-based classifier.

    Its keys are the user's, so no rule about names can classify what is
    inside it. Deriving the set from the field types means a mapping field
    added later is withheld from the day it is added rather than from the
    day somebody remembers it.
    """
    mappings = {f.name for f in fields(ClusterConfig) if _is_opaque_mapping(f.type)}

    assert mappings == UNCLASSIFIABLE_FIELDS
    assert mappings == {"environment_variables", "gpu_requirements", "venv_info"}


@pytest.mark.parametrize("field_name", ["gpu_requirements", "venv_info"])
def test_a_secret_nested_one_level_down_does_not_reach_disk(field_name, tmp_path):
    """The reproduction. ``strip_secret_fields`` does not descend.

    ``gpu_requirements`` and ``venv_info`` are opaque dictionaries exactly
    like ``environment_variables``, and were not withheld, so a credential
    inside either went to disk verbatim under a top-level key the allowlist
    approves of.

    Recursion was the other candidate fix and is the wrong one: it would
    classify the nested keys by name, which is the approach issue #167
    replaced, and a nested ``{"license_blob": <a token>}`` defeats it.
    """
    planted = "-".join(["clustrix", "sentinel", field_name, "value"])
    path = tmp_path / "config.yml"
    config = ClusterConfig(cluster_type="ssh")
    setattr(config, field_name, {"api_key": planted})

    config.save_to_file(str(path))

    assert planted not in path.read_text(encoding="utf-8")

    # And the opt-in still writes it, so nothing was made unreachable.
    opted_in = tmp_path / "with-secrets.yml"
    config.save_to_file(str(opted_in), include_secrets=True)
    assert planted in opted_in.read_text(encoding="utf-8")


# --------------------------------------------------------------------------
# The classifier's domain.
# --------------------------------------------------------------------------


def test_the_classifier_refuses_a_name_it_was_not_written_for():
    """Mutant M10's replacement, and a guard that can actually fail.

    ``NOT_SECRET_FIELDS`` used to freeze the exemption regex against the
    declared field names, and that freeze was **vacuous**: this classifier
    has one caller, the ``SECRET_FIELDS`` comprehension, which only ever
    passes declared names, so the frozen set equalled the regex by
    construction. Unfreezing ``^use_`` changed nothing and the mutant
    survived. The domain restriction is enforced here instead.
    """
    with pytest.raises(ValueError, match="not a ClusterConfig field"):
        _is_secret_field("USE_PASSWORD")

    with pytest.raises(ValueError, match="not a ClusterConfig field"):
        _is_secret_field("SSH_PASSPHRASE")


def test_the_two_exemptions_still_apply_to_the_fields_they_were_written_for():
    """Dropping either breaks the auth-fallback round trip.

    ``use_env_password`` is a boolean flag and ``password_env_var`` holds
    the *name* of an environment variable; neither is a credential, and
    withholding them would stop a configuration that supplies its password
    through the environment from surviving a save.
    """
    assert _is_secret_field("use_env_password") is False
    assert _is_secret_field("password_env_var") is False
    assert "use_env_password" in PERSISTABLE_KEYS
    assert "password_env_var" in PERSISTABLE_KEYS
    assert "use_env_password" not in SECRET_FIELDS
    assert "password_env_var" not in SECRET_FIELDS


def test_the_declared_names_really_are_the_dataclass():
    assert DECLARED_FIELD_NAMES == FIELD_NAMES


def test_the_credential_fields_are_still_classified_as_secret():
    """The exemptions must not have swallowed the thing they sit next to."""
    assert {"password", "api_key", "hf_token"} <= SECRET_FIELDS
    assert SECRET_FIELDS <= FIELD_NAMES


# --------------------------------------------------------------------------
# The mapping detector has to describe mappings, not enumerate ``dict``.
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "annotation",
    [
        dict,
        Dict[str, str],
        Optional[Dict[str, str]],
        Mapping[str, str],
        Optional[Mapping[str, str]],
        MutableMapping[str, str],
        Optional[MutableMapping[str, str]],
        collections.abc.Mapping,
        Union[Dict[str, str], None],
        List[Dict[str, str]],
        Any,
        Optional[Any],
        object,
        "Dict[str, str]",
    ],
)
def test_every_spelling_of_a_mapping_is_opaque(annotation):
    """The bug this replaces asked ``issubclass(origin, dict)``.

    That caught ``Dict[str, str]`` and a bare ``dict`` and missed
    ``Mapping``, ``MutableMapping`` and ``Any``. It was verified by really
    adding a field: ``mapping_typed: Optional[Mapping[str, str]] =
    {"api_key": ...}`` put the value on disk, which is precisely the failure
    ``UNCLASSIFIABLE_FIELDS`` exists to prevent -- the same bug class, one
    annotation away.

    Enumerating spellings is what name-based secret classification did, and
    it failed the same way. ``collections.abc.Mapping`` is the interface all
    of these name; ``Any``, ``object`` and an unresolved string annotation
    do not constrain the value at all, so a mapping cannot be ruled out and
    the fail-closed answer is the only sound one.
    """
    assert _is_opaque_mapping(annotation)


@pytest.mark.parametrize(
    "annotation",
    [
        str,
        Optional[str],
        int,
        bool,
        Optional[int],
        List[str],
        Optional[List[str]],
    ],
)
def test_a_field_that_cannot_hold_a_mapping_is_still_written(annotation):
    """And prove the widening did not swallow everything.

    A detector that answers True for every annotation would pass the test
    above and withhold the entire configuration file.
    """
    assert not _is_opaque_mapping(annotation)


def test_the_derivation_picks_up_a_mapping_field_added_later():
    """The escape was found by really adding a field; this is that shape.

    ``UNCLASSIFIABLE_FIELDS`` is ``{f.name for f in fields(ClusterConfig) if
    _is_opaque_mapping(f.type)}``. Run the identical derivation over a
    dataclass carrying the annotation that escaped -- ``Optional[Mapping[str,
    str]]``, whose value went to disk verbatim -- and the field has to come
    out withheld, without permanently adding one to the shipped
    configuration.
    """

    @dataclasses.dataclass
    class ConfigWithAMappingField:
        cluster_type: str = "local"
        remote_work_dir: str = "/tmp"
        mapping_typed: Optional[Mapping[str, str]] = None
        anything: Any = None

    withheld = {
        f.name for f in fields(ConfigWithAMappingField) if _is_opaque_mapping(f.type)
    }

    assert withheld == {"mapping_typed", "anything"}
