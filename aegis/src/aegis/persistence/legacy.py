"""Converting the old one-pickle-per-tenant models into the safe format.

Until this migration a trained model was stored as `pickle.dumps(model)` in a text column, and
`pickle.loads` on a database field is code execution for anyone who can write that column. The
conversion therefore never calls `pickle.loads`: it uses an unpickler that refuses every global
that is not on an exact allow-list of the numpy and scikit-learn classes a fitted pipeline is made
of, so a doctored row fails to convert instead of running anything. What comes out is re-stored as
plain arrays, signed, and the legacy table is left untouched for the operator to drop later.
"""

from __future__ import annotations

import base64
import hashlib
import io
import logging
import pickle
from collections.abc import Callable
from typing import Any, cast

import numpy as np
from sqlalchemy import Connection, Table, insert, select

from aegis.attrition.features import FEATURE_NAMES
from aegis.attrition.frozen import FrozenPipeline
from aegis.attrition.model import AttritionModel, ModelError
from aegis.persistence.models import ModelRow, ModelVersionRow
from aegis.security.signing import model_signing_key, sign

logger = logging.getLogger("aegis.migration")

ALLOWED_GLOBALS: frozenset[tuple[str, str]] = frozenset(
    {
        ("aegis.attrition.model", "AttritionModel"),
        ("numpy", "dtype"),
        ("numpy", "ndarray"),
        ("numpy._core.multiarray", "_reconstruct"),
        ("numpy._core.multiarray", "scalar"),
        ("numpy.core.multiarray", "_reconstruct"),
        ("numpy.core.multiarray", "scalar"),
        ("numpy.random._mt19937", "MT19937"),
        ("numpy.random._pickle", "__bit_generator_ctor"),
        ("numpy.random._pickle", "__randomstate_ctor"),
        ("sklearn._loss._loss", "CyHalfBinomialLoss"),
        ("sklearn._loss.link", "Interval"),
        ("sklearn._loss.link", "LogitLink"),
        ("sklearn._loss.loss", "HalfBinomialLoss"),
        ("sklearn.dummy", "DummyClassifier"),
        ("sklearn.ensemble._forest", "RandomForestClassifier"),
        ("sklearn.ensemble._gb", "GradientBoostingClassifier"),
        ("sklearn.linear_model._logistic", "LogisticRegression"),
        ("sklearn.pipeline", "Pipeline"),
        ("sklearn.preprocessing._data", "StandardScaler"),
        ("sklearn.tree._classes", "DecisionTreeClassifier"),
        ("sklearn.tree._classes", "DecisionTreeRegressor"),
        ("sklearn.tree._tree", "Tree"),
    }
)


class LegacyPickleError(ModelError):
    pass


class _AllowListUnpickler(pickle.Unpickler):
    def find_class(self, module: str, name: str) -> Any:
        if (module, name) not in ALLOWED_GLOBALS:
            raise LegacyPickleError(
                f"legacy model refused: it references {module}.{name}, which is not part of a "
                "fitted scikit-learn pipeline"
            )
        return super().find_class(module, name)


def restricted_loads(data: bytes) -> Any:
    try:
        return _AllowListUnpickler(io.BytesIO(data)).load()
    except LegacyPickleError:
        raise
    except Exception as error:
        raise LegacyPickleError(f"legacy model could not be read: {error}") from error


def convert_pickle(payload_b64: str) -> AttritionModel:
    """A legacy payload as a model in the safe format (without a drift reference: the old rows
    never kept one)."""
    raw = base64.b64decode(payload_b64)
    legacy = restricted_loads(raw)
    pipeline = getattr(legacy, "_pipeline", None)
    baseline = getattr(legacy, "_baseline", None)
    importance = getattr(legacy, "_importance", None)
    algorithm = getattr(legacy, "_algorithm", None)
    if pipeline is None or baseline is None or importance is None or algorithm is None:
        raise LegacyPickleError("legacy model is missing its fitted state")

    model = AttritionModel(str(algorithm))
    model._frozen = FrozenPipeline.from_sklearn(pipeline)
    model._baseline = np.asarray(baseline, dtype=float)
    model._importance = tuple((str(n), float(w)) for n, w in importance)
    model._reference = np.empty((0, len(FEATURE_NAMES)), dtype=float)
    model._data_hash = hashlib.sha256(b"legacy:" + raw).hexdigest()
    return model


LEGACY_FIDELITY = {
    "gate": "WARN",
    "score": None,
    "findings": [
        "converted from a legacy pickle; it was never fidelity-gated and carries no drift reference"
    ],
}


def convert_legacy_rows(
    connection: Connection, key: bytes | None = None, on_skip: Callable[[str], None] | None = None
) -> int:
    """Copy every row of the legacy table into attrition_model_versions as version 1. Rows that
    cannot be converted are skipped and reported, never half-written. Returns how many converted."""
    signing_key = key or model_signing_key()
    legacy = cast("Table", ModelRow.__table__)
    target = cast("Table", ModelVersionRow.__table__)
    already = {row.tenant_id for row in connection.execute(select(target.c.tenant_id).distinct())}
    converted = 0
    for row in connection.execute(select(legacy)).mappings():
        tenant = row["tenant_id"]
        if tenant in already:
            continue
        try:
            model = convert_pickle(row["payload"])
            blob = model.to_bytes()
        except (ModelError, ValueError) as error:
            message = f"legacy model for tenant {tenant} not converted: {error}"
            logger.warning(message)
            if on_skip:
                on_skip(message)
            continue
        digest = hashlib.sha256(blob).hexdigest()
        signature = sign(
            signing_key, "aegis-model-v1", tenant, "1", "WARN", digest, model.data_hash
        )
        connection.execute(
            insert(target).values(
                tenant_id=tenant,
                version=1,
                algorithm=row["algorithm"],
                rows=row["rows"],
                positives=row["positives"],
                feature_importance=row["feature_importance"],
                data_hash=model.data_hash,
                format="npz-v1",
                payload=base64.b64encode(blob).decode("ascii"),
                signature=signature,
                gate="WARN",
                fidelity=LEGACY_FIDELITY,
                active=True,
                created_by="migration",
                created_at=row["trained_at"],
                activated_at=row["trained_at"],
            )
        )
        converted += 1
    return converted
