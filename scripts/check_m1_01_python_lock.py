#!/usr/bin/env python3
"""Validate the frozen M1-01 Python wheel lock and its evidence boundary."""

from __future__ import annotations

import argparse
import copy
import hashlib
import importlib
import importlib.metadata
import json
import platform
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse


ROOT = Path(__file__).resolve().parents[1]
EVIDENCE_PATH = ROOT / "validation/results/m1-01-python-runtime-lock.json"
LOCK_PATH = ROOT / "requirements/m1-01-runtime.txt"
DIRECT_PATH = ROOT / "requirements/m1-01-runtime.in"
SBOM_PATH = ROOT / "sbom/m1-01-python-runtime.cdx.json"
NOTICES_PATH = ROOT / "THIRD_PARTY_NOTICES.md"
ADMISSION_PATH = ROOT / "validation/results/m1-01-runtime-dependency-admission.json"
DECISION_PATH = ROOT / "docs/M1_01_RUNTIME_DEPENDENCY_DECISION.md"

EXPECTED_DIRECT = {
    "asyncpg": "0.31.0",
    "fastapi": "0.141.1",
    "python-multipart": "0.0.32",
    "uvicorn": "0.52.1",
}

# name: version, filename, sha256, license, retained license path, wheel license sha256, URL
EXPECTED_ARTIFACTS = {
    "annotated-doc": ("0.0.4", "annotated_doc-0.0.4-py3-none-any.whl", "571ac1dc6991c450b25a9c2d84a3705e2ae7a53467b5d111c24fa8baabbed320", "MIT", "third_party/licenses/python/annotated-doc-0.0.4/LICENSE", "fff170779a6acbf65abdb405c087f1cee1786691e4a96a4034517e4a504a0cdf", "https://files.pythonhosted.org/packages/1e/d3/26bf1008eb3d2daa8ef4cacc7f3bfdc11818d111f7e2d0201bc6e3b49d45/annotated_doc-0.0.4-py3-none-any.whl"),
    "annotated-types": ("0.7.0", "annotated_types-0.7.0-py3-none-any.whl", "1f02e8b43a8fbbc3f3e0d4f0f4bfc8131bcb4eebe8849b8e5c773f3a1c582a53", "MIT", "third_party/licenses/python/annotated-types-0.7.0/LICENSE", "fe1049884b1a0d9342901e88e07f32925d24b3121d9972b6a6805fb9824b095d", "https://files.pythonhosted.org/packages/78/b6/6307fbef88d9b5ee7421e68d78a9f162e0da4900bc5f5793f6d3d0e34fb8/annotated_types-0.7.0-py3-none-any.whl"),
    "anyio": ("4.12.1", "anyio-4.12.1-py3-none-any.whl", "d405828884fc140aa80a3c667b8beed277f1dfedec42ba031bd6ac3db606ab6c", "MIT", "third_party/licenses/python/anyio-4.12.1/LICENSE", "5361ac9dc58f2ef5fd2e9b09c68297c17f04950909bbc8023bdb82eacf22c2b0", "https://files.pythonhosted.org/packages/38/0e/27be9fdef66e72d64c0cdc3cc2823101b80585f8119b5c112c2e8f5f7dab/anyio-4.12.1-py3-none-any.whl"),
    "asyncpg": ("0.31.0", "asyncpg-0.31.0-cp312-cp312-manylinux_2_28_x86_64.whl", "aad7a33913fb8bcb5454313377cc330fbb19a0cd5faa7272407d8a0c4257b671", "Apache-2.0", "third_party/licenses/python/asyncpg-0.31.0/LICENSE", "d9222d73fdac50992174076efa04f44f6fbcd9d56155a7c7092e98757042a6f6", "https://files.pythonhosted.org/packages/8c/d1/a867c2150f9c6e7af6462637f613ba67f78a314b00db220cd26ff559d532/asyncpg-0.31.0-cp312-cp312-manylinux_2_28_x86_64.whl"),
    "click": ("8.2.1", "click-8.2.1-py3-none-any.whl", "61a3265b914e850b85317d0b3109c7f8cd35a670f963866005d6ef1d5175a12b", "BSD-3-Clause", "third_party/licenses/python/click-8.2.1/LICENSE.txt", "9a8ad106a394e853bfe21f42f4e72d592819a22805d991b5f3275029292b658d", "https://files.pythonhosted.org/packages/85/32/10bb5764d90a8eee674e9dc6f4db6a0ab47c8c4d0d83c27f7c39ac415a4d/click-8.2.1-py3-none-any.whl"),
    "fastapi": ("0.141.1", "fastapi-0.141.1-py3-none-any.whl", "bfb91aa2d334c61cb35ba9a116fc123b3d3df31640b801cf57a7a78ec3f603b3", "MIT", "third_party/licenses/python/fastapi-0.141.1/LICENSE", "4ec89ffc81485b97fec584b2d4a961032eeffe834453894fd9c1274906cc744e", "https://files.pythonhosted.org/packages/cb/03/10388a42375ee7e4ac9b94eb2c5c569c8b5795e377e701c9ac3ad63de890/fastapi-0.141.1-py3-none-any.whl"),
    "h11": ("0.16.0", "h11-0.16.0-py3-none-any.whl", "63cf8bbe7522de3bf65932fda1d9c2772064ffb3dae62d55932da54b31cb6c86", "MIT", "third_party/licenses/python/h11-0.16.0/LICENSE.txt", "37db5bb85926db28a427a25867f10b1232003aea1be69ccb851138adb8e6f361", "https://files.pythonhosted.org/packages/04/4b/29cac41a4d98d144bf5f6d33995617b185d14b22401f75ca86f384e87ff1/h11-0.16.0-py3-none-any.whl"),
    "idna": ("3.18", "idna-3.18-py3-none-any.whl", "7f952cbe720b688055e3f87de14f5c3e5fdaa8bc3928985c4077ca689de849a2", "BSD-3-Clause", "third_party/licenses/python/idna-3.18/LICENSE.md", "1a9a4f0e3d479a27240ddd59a9137a66ab4a0f9dfdc8ca6188cc0bfd85187f04", "https://files.pythonhosted.org/packages/1e/5e/d4e9f1a599fb8e573b7b87160658329fbf28d19eac2718f51fc3def3aa5a/idna-3.18-py3-none-any.whl"),
    "pydantic": ("2.13.4", "pydantic-2.13.4-py3-none-any.whl", "45a282cde31d808236fd7ea9d919b128653c8b38b393d1c4ab335c62924d9aba", "MIT", "third_party/licenses/python/pydantic-2.13.4/LICENSE", "a9e186f3ca16b5eef84318e7a701721351a00cb7b8ae3a4394b67b49e3529ef3", "https://files.pythonhosted.org/packages/fd/7b/122376b1fd3c62c1ed9dc80c931ace4844b3c55407b6fb2d199377c9736f/pydantic-2.13.4-py3-none-any.whl"),
    "pydantic-core": ("2.46.4", "pydantic_core-2.46.4-cp312-cp312-manylinux_2_17_x86_64.manylinux2014_x86_64.whl", "926c9541b14b12b1681dca8a0b75feb510b06c6341b70a8e500c2fdcff837cce", "MIT", "third_party/licenses/python/pydantic-core-2.46.4/LICENSE", "2afdd30d54b4d62b6f488a6bcc1546e84ec5061f13f4209c03d012348783795a", "https://files.pythonhosted.org/packages/5f/97/2aab507d3d00ca626e8e57c1eac6a79e4e5fbcc63eb99733ff55d1717f65/pydantic_core-2.46.4-cp312-cp312-manylinux_2_17_x86_64.manylinux2014_x86_64.whl"),
    "python-multipart": ("0.0.32", "python_multipart-0.0.32-py3-none-any.whl", "ff6d3f776f16878c894e52e107296ffc890e913c611b1a4ec6c44e2821fe2e23", "Apache-2.0", "third_party/licenses/python/python-multipart-0.0.32/LICENSE.txt", "cfc7749b96f63bd31c3c42b5c471bf756814053e847c10f3eb003417bc523d30", "https://files.pythonhosted.org/packages/e1/04/e8135ebd1ad02c56ec633277529b2602ff99ff634be76cdba5744cf554fd/python_multipart-0.0.32-py3-none-any.whl"),
    "starlette": ("1.6.0", "starlette-1.6.0-py3-none-any.whl", "a86dd39d14bb45f85a3d18525215a9ef0cfd1f192ac793220e72598c90335f0c", "BSD-3-Clause", "third_party/licenses/python/starlette-1.6.0/LICENSE.md", "dcb95677a02240243187e964f941847d19b17821cf99e5afae684fab328c19bf", "https://files.pythonhosted.org/packages/c8/cb/6a6a47d5b464bd08695d254f3da6e7986cc70c9fa5d778eda57538edfe56/starlette-1.6.0-py3-none-any.whl"),
    "typing-extensions": ("4.16.0", "typing_extensions-4.16.0-py3-none-any.whl", "481caa481374e813c1b176ada14e97f1f67a4539ce9cfeb3f350d78d6370c2e8", "PSF-2.0", "third_party/licenses/python/typing-extensions-4.16.0/LICENSE", "3b2f81fe21d181c499c59a256c8e1968455d6689d269aa85373bfb6af41da3bf", "https://files.pythonhosted.org/packages/49/d3/b8441a820a491ddfc024b0b0cf0393375b75ea13866d9c66727e54c2fc80/typing_extensions-4.16.0-py3-none-any.whl"),
    "typing-inspection": ("0.4.2", "typing_inspection-0.4.2-py3-none-any.whl", "4ed1cacbdc298c220f1bd249ed5287caa16f34d44ef4e9c3d0cbad5b521545e7", "MIT", "third_party/licenses/python/typing-inspection-0.4.2/LICENSE", "804b59b25f2c31bd278f9202a19ae49a3945aa2664387e2d0a128c7cacc61ec3", "https://files.pythonhosted.org/packages/dc/9b/47798a6c91d8bdb567fe2698fe81e0c6b7cb7ef4d13da4114b41d239f65d/typing_inspection-0.4.2-py3-none-any.whl"),
    "uvicorn": ("0.52.1", "uvicorn-0.52.1-py3-none-any.whl", "e4403f9d93188cf9d1088e9f40e3acd12630e2df8675316704379a7fc20fff6a", "BSD-3-Clause", "third_party/licenses/python/uvicorn-0.52.1/LICENSE.md", "efe1acf3e62fb99c288b0ec73e5a773b7268ef4320fe757ea994214e4b63c371", "https://files.pythonhosted.org/packages/c7/d5/68e6e9bca63c0badf67002890a46d3784c958de45b65e1275ec583ca1f06/uvicorn-0.52.1-py3-none-any.whl"),
}

EXPECTED_GRAPH = {
    "annotated-doc": [],
    "annotated-types": [],
    "anyio": ["idna", "typing-extensions"],
    "asyncpg": [],
    "click": [],
    "fastapi": ["annotated-doc", "pydantic", "starlette", "typing-extensions", "typing-inspection"],
    "h11": [],
    "idna": [],
    "pydantic": ["annotated-types", "pydantic-core", "typing-extensions", "typing-inspection"],
    "pydantic-core": ["typing-extensions"],
    "python-multipart": [],
    "starlette": ["anyio", "typing-extensions"],
    "typing-extensions": [],
    "typing-inspection": ["typing-extensions"],
    "uvicorn": ["click", "h11"],
}

EXPECTED_NATIVE = {
    "asyncpg": [
        "asyncpg/pgproto/pgproto.cpython-312-x86_64-linux-gnu.so",
        "asyncpg/protocol/protocol.cpython-312-x86_64-linux-gnu.so",
        "asyncpg/protocol/record.cpython-312-x86_64-linux-gnu.so",
    ],
    "pydantic-core": ["pydantic_core/_pydantic_core.cpython-312-x86_64-linux-gnu.so"],
}

EXPECTED_SIZES = {
    "annotated-doc": 5303,
    "annotated-types": 13643,
    "anyio": 113592,
    "asyncpg": 3520321,
    "click": 102215,
    "fastapi": 131954,
    "h11": 37515,
    "idna": 65455,
    "pydantic": 472262,
    "pydantic-core": 2094516,
    "python-multipart": 30042,
    "starlette": 75969,
    "typing-extensions": 45571,
    "typing-inspection": 14611,
    "uvicorn": 79859,
}

EXPECTED_WHEEL_LICENSE_PATHS = {
    "annotated-doc": "annotated_doc-0.0.4.dist-info/licenses/LICENSE",
    "annotated-types": "annotated_types-0.7.0.dist-info/licenses/LICENSE",
    "anyio": "anyio-4.12.1.dist-info/licenses/LICENSE",
    "asyncpg": "asyncpg-0.31.0.dist-info/licenses/LICENSE",
    "click": "click-8.2.1.dist-info/licenses/LICENSE.txt",
    "fastapi": "fastapi-0.141.1.dist-info/licenses/LICENSE",
    "h11": "h11-0.16.0.dist-info/licenses/LICENSE.txt",
    "idna": "idna-3.18.dist-info/licenses/LICENSE.md",
    "pydantic": "pydantic-2.13.4.dist-info/licenses/LICENSE",
    "pydantic-core": "pydantic_core-2.46.4.dist-info/licenses/LICENSE",
    "python-multipart": "python_multipart-0.0.32.dist-info/licenses/LICENSE.txt",
    "starlette": "starlette-1.6.0.dist-info/licenses/LICENSE.md",
    "typing-extensions": "typing_extensions-4.16.0.dist-info/licenses/LICENSE",
    "typing-inspection": "typing_inspection-0.4.2.dist-info/licenses/LICENSE",
    "uvicorn": "uvicorn-0.52.1.dist-info/licenses/LICENSE.md",
}

EXPECTED_EXCLUDED = [
    {
        "name": "async-timeout",
        "reason": "asyncpg marker python_version < 3.11.0 is false for Python 3.12.13",
    },
    {
        "name": "colorama",
        "reason": "Click marker platform_system == Windows is false on Linux",
    },
    {
        "name": "exceptiongroup",
        "reason": "AnyIO marker python_version < 3.11 is false for Python 3.12.13",
    },
    {
        "name": "sniffio",
        "reason": "AnyIO 4.12.1 wheel metadata does not declare sniffio",
    },
    {
        "name": "all extras",
        "reason": "The direct manifest contains no extras and the closure evaluates with extra set to empty",
    },
]

EXPECTED_NATIVE_FINDINGS = [
    "asyncpg contains three CPython 3.12 Linux ELF extension modules and one Apache-2.0 LICENSE entry; no NOTICE or separately packaged shared-library entry was found.",
    "pydantic-core contains one CPython 3.12 Linux ELF extension module and one MIT LICENSE entry; no NOTICE or separately packaged shared-library entry was found.",
]

EXPECTED_ACCEPTED_PLATFORMS = [
    "manylinux_2_28_x86_64",
    "manylinux_2_17_x86_64",
    "py3-none-any",
]
EXPECTED_TARGET_ARGUMENTS = [
    "--implementation",
    "cp",
    "--python-version",
    "3.12",
    "--abi",
    "cp312",
    "--platform",
    "manylinux_2_28_x86_64",
    "--platform",
    "manylinux_2_17_x86_64",
]

STATIC_COMMAND = "python scripts/check_m1_01_python_lock.py --static"
INSTALL_COMMAND = (
    "python -m pip install --force-reinstall --no-deps --require-hashes "
    "--only-binary=:all: "
    "-r requirements/m1-01-runtime.txt"
)
INSTALLED_COMMAND = "python scripts/check_m1_01_python_lock.py --installed"
PIP_CHECK_COMMAND = "python -m pip check"
FRESH_ENVIRONMENT_REQUIREMENT = (
    "Run the install and installed checks in a newly created disposable CPython "
    "3.12.13 virtual environment inside a clean Linux runner or container, with "
    "no application distributions preinstalled."
)
EXPECTED_GATES = {
    "exactWheelLock": STATIC_COMMAND,
    "activeClosure": STATIC_COMMAND,
    "licenseAndNotice": STATIC_COMMAND,
    "logicalSbom": STATIC_COMMAND,
    "cleanLinuxInstall": INSTALL_COMMAND,
    "runtimeImports": INSTALLED_COMMAND,
    "pipCheck": PIP_CHECK_COMMAND,
}
EXPECTED_GATE_PLATFORM = "Linux-x86_64"
EXPECTED_IMPORTS = {
    "annotated-doc": "annotated_doc",
    "annotated-types": "annotated_types",
    "anyio": "anyio",
    "asyncpg": "asyncpg",
    "click": "click",
    "fastapi": "fastapi",
    "h11": "h11",
    "idna": "idna",
    "pydantic": "pydantic",
    "pydantic-core": "pydantic_core",
    "python-multipart": "multipart",
    "starlette": "starlette",
    "typing-extensions": "typing_extensions",
    "typing-inspection": "typing_inspection",
    "uvicorn": "uvicorn",
}

EXPECTED_ADMISSION_SHA256 = "403d2b87a72ac90c38ac2aa12cc2b447b7a0389cef5be31e16c951d6a751e745"
EXPECTED_DECISION_SHA256 = "e219a260143d8adc3cdeb2d2c94eedefad2dfb5cf40140bc0ab0459f6f5b99a3"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_json(path: Path, failures: list[str]) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        failures.append(f"{path.relative_to(ROOT)} is unreadable JSON: {exc}")
        return {}
    if not isinstance(value, dict):
        failures.append(f"{path.relative_to(ROOT)} root must be an object")
        return {}
    return value


def parse_utc_timestamp(value: object, label: str, failures: list[str]) -> datetime | None:
    if not isinstance(value, str) or not re.fullmatch(
        r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z", value
    ):
        failures.append(f"{label} must be an RFC 3339 UTC timestamp")
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        failures.append(f"{label} is not a valid timestamp")
        return None


def validate_gates(evidence: dict) -> list[str]:
    failures: list[str] = []
    gates = evidence.get("gates")
    if not isinstance(gates, dict):
        return ["evidence gates must be an object"]
    if set(gates) != set(EXPECTED_GATES):
        failures.append(
            f"evidence gate names must be exactly {list(EXPECTED_GATES)}"
        )
    for name in EXPECTED_GATES:
        gate = gates.get(name)
        if not isinstance(gate, dict):
            failures.append(f"gate {name} must be an object")
            continue
        status = gate.get("status")
        if status == "blocked":
            if set(gate) != {"status", "blocker"}:
                failures.append(f"blocked gate {name} has unexpected or missing fields")
            if not isinstance(gate.get("blocker"), str) or not gate["blocker"].strip():
                failures.append(f"blocked gate {name} requires a concrete blocker")
            continue
        if status == "passed":
            failures.append(
                f"committed static evidence cannot self-attest passed gate {name}; "
                "trusted external CI attestation is required"
            )
            continue
        if status != "blocked":
            failures.append(f"gate {name} status must be passed or blocked")
    if evidence.get("overallStatus") != "blocked":
        failures.append("overallStatus must remain blocked in committed static evidence")
    return failures


def validate_direct(text: str) -> list[str]:
    failures: list[str] = []
    entries: dict[str, str] = {}
    for line_number, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "[" in line or ";" in line or " @ " in line or line.startswith("-"):
            failures.append(f"direct manifest line {line_number} uses an extra, marker, URL, or option")
            continue
        match = re.fullmatch(r"([a-z0-9-]+)==([0-9]+(?:\.[0-9]+){1,3})", line)
        if not match:
            failures.append(f"direct manifest line {line_number} is not an exact package==version pin")
            continue
        name, version = match.groups()
        if name in entries:
            failures.append(f"direct manifest duplicates {name}")
        entries[name] = version
    if entries != EXPECTED_DIRECT:
        failures.append(f"direct manifest drift: expected {EXPECTED_DIRECT}, got {entries}")
    return failures


def validate_lock(text: str) -> tuple[list[str], dict[str, tuple[str, str]]]:
    failures: list[str] = []
    logical = re.sub(r"\\\r?\n\s*", "", text)
    entries: dict[str, tuple[str, str]] = {}
    required_options = {"--require-hashes", "--only-binary=:all:"}
    seen_options: set[str] = set()
    for line_number, raw in enumerate(logical.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("--"):
            seen_options.add(line)
            continue
        if "[" in line or ";" in line:
            failures.append(f"lock line {line_number} contains an extra or environment marker")
            continue
        match = re.fullmatch(
            r"([a-z0-9-]+) @ (https://[^ ]+\.whl)\s+--hash=sha256:([0-9a-f]{64})",
            line,
        )
        if not match:
            failures.append(f"lock line {line_number} is not an exact hashed wheel URL")
            continue
        name, url, digest = match.groups()
        parsed = urlparse(url)
        if parsed.scheme != "https" or parsed.netloc != "files.pythonhosted.org" or parsed.query or parsed.fragment:
            failures.append(f"{name} has a non-canonical artifact URL")
        if name in entries:
            failures.append(f"lock duplicates {name}")
        entries[name] = (url, digest)
    if seen_options != required_options:
        failures.append(f"lock options drift: expected {required_options}, got {seen_options}")
    if set(entries) != set(EXPECTED_ARTIFACTS):
        failures.append("lock package set does not match the frozen 15-package closure")
    for name, expected in EXPECTED_ARTIFACTS.items():
        if entries.get(name) != (expected[6], expected[2]):
            failures.append(f"{name} lock URL or SHA-256 drift")
    return failures, entries


def validate_evidence(evidence: dict) -> list[str]:
    failures: list[str] = []
    expected_top_level = {
        "schemaVersion",
        "taskId",
        "workPackage",
        "baselineCommit",
        "generatedAt",
        "overallStatus",
        "claimBoundary",
        "installedEnvironmentRequirement",
        "inputs",
        "runtime",
        "artifactSelection",
        "directRequirements",
        "packages",
        "activeDependencyGraph",
        "excluded",
        "wheelInspection",
        "gates",
        "requiredLinuxAcceptanceCommands",
        "blockingReasons",
    }
    if set(evidence) != expected_top_level:
        failures.append("evidence top-level schema has missing or unexpected fields")
    expected_scalars = {
        "schemaVersion": 1,
        "taskId": "M1-01",
        "workPackage": "WP3",
        "baselineCommit": "f9e81810e6e374e9047c36c53c480bcf6fbfd553",
    }
    for key, expected in expected_scalars.items():
        if evidence.get(key) != expected:
            failures.append(f"evidence {key} must be {expected!r}")
    expected_claim_boundary = (
        "This record proves artifact selection, hashes, wheel metadata closure, and "
        "retained license evidence only. It does not prove a Linux installation, pip "
        "check, PostgreSQL behavior, production readiness, or M1-01 completion."
    )
    if evidence.get("claimBoundary") != expected_claim_boundary:
        failures.append("evidence claimBoundary drift")
    if evidence.get("installedEnvironmentRequirement") != FRESH_ENVIRONMENT_REQUIREMENT:
        failures.append("installed environment requirement drift")
    parse_utc_timestamp(evidence.get("generatedAt"), "evidence generatedAt", failures)
    expected_inputs = {
        "admissionPath": "validation/results/m1-01-runtime-dependency-admission.json",
        "admissionSha256": EXPECTED_ADMISSION_SHA256,
        "decisionPath": "docs/M1_01_RUNTIME_DEPENDENCY_DECISION.md",
        "decisionSha256": EXPECTED_DECISION_SHA256,
    }
    if evidence.get("inputs") != expected_inputs:
        failures.append("evidence input paths or hashes drift")
    runtime = evidence.get("runtime", {})
    expected_runtime = {
        "pythonVersion": "3.12.13",
        "implementation": "CPython",
        "pythonTag": "cp312",
        "operatingSystem": "Linux",
        "architecture": "x86_64",
        "minimumGlibc": "2.28",
        "acceptedWheelPlatforms": EXPECTED_ACCEPTED_PLATFORMS,
        "pipVersion": "26.0.1",
    }
    if not isinstance(runtime, dict) or set(runtime) != set(expected_runtime):
        failures.append("runtime schema has missing or unexpected fields")
        runtime = {}
    for key, expected in expected_runtime.items():
        if runtime.get(key) != expected:
            failures.append(f"runtime {key} must be {expected}")
    artifact_selection = evidence.get("artifactSelection", {})
    expected_artifact_selection = {
        "status": "passed",
        "reportMode": "pip install --dry-run --ignore-installed --no-deps --only-binary=:all:",
        "reportSha256": "fa1d57df1b312dcb3678c7b0b6afe8e27e33bbf57c47aae76c9676ba1888e9eb",
        "reportSize": 229478,
        "reportRetained": False,
        "reportRetentionReason": "The 229478-byte transient pip report duplicates package metadata. Its selected URL, filename, size, and SHA-256 fields are retained below and independently matched to downloaded wheel bytes.",
        "targetArguments": EXPECTED_TARGET_ARGUMENTS,
        "limitation": "All exact closure pins were passed to the artifact-only report because pip cross-target mode on a Windows host evaluates platform_system markers against Windows. That host report selected colorama incorrectly and was rejected as Linux resolution evidence.",
    }
    if artifact_selection != expected_artifact_selection:
        failures.append("artifactSelection schema or frozen values drift")
    if evidence.get("directRequirements") != [f"{name}=={version}" for name, version in EXPECTED_DIRECT.items()]:
        failures.append("evidence directRequirements drift")
    packages = evidence.get("packages")
    if not isinstance(packages, list):
        return failures + ["evidence packages must be an array"]
    by_name = {item.get("name"): item for item in packages if isinstance(item, dict)}
    if [item.get("name") for item in packages if isinstance(item, dict)] != list(
        EXPECTED_ARTIFACTS
    ):
        failures.append("evidence package ordering drift")
    if len(by_name) != len(packages):
        failures.append("evidence packages contain a duplicate or invalid item")
    if set(by_name) != set(EXPECTED_ARTIFACTS):
        failures.append("evidence package set does not match frozen closure")
    for name, expected in EXPECTED_ARTIFACTS.items():
        item = by_name.get(name, {})
        expected_fields = {
            "version": expected[0],
            "filename": expected[1],
            "sha256": expected[2],
            "license": expected[3],
            "retainedLicensePath": expected[4],
            "wheelLicenseSha256": expected[5],
            "url": expected[6],
            "role": "direct" if name in EXPECTED_DIRECT else "transitive",
            "nativeEntries": EXPECTED_NATIVE.get(name, []),
            "wheelLicensePath": EXPECTED_WHEEL_LICENSE_PATHS[name],
            "size": EXPECTED_SIZES[name],
        }
        expected_package_keys = {"name", *expected_fields}
        if set(item) != expected_package_keys:
            failures.append(f"evidence {name} has missing or unexpected fields")
        for key, value in expected_fields.items():
            if item.get(key) != value:
                failures.append(f"evidence {name}.{key} drift")
    graph = evidence.get("activeDependencyGraph")
    if graph != EXPECTED_GRAPH:
        failures.append("evidence activeDependencyGraph drift")
    if evidence.get("excluded") != EXPECTED_EXCLUDED:
        failures.append("evidence excluded package names or reasons drift")
    inspection = evidence.get("wheelInspection", {})
    expected_inspection = {
        "status": "passed",
        "artifactHashesMatchedDownloadedBytes": True,
        "licenseEntriesRetained": True,
        "noticeEntriesFound": [],
        "nativeWheelFindings": EXPECTED_NATIVE_FINDINGS,
        "limitation": "This is a wheel-container inspection, not a source-level inventory of statically linked compiler dependencies or a legal opinion.",
    }
    if inspection != expected_inspection:
        failures.append("wheel inspection schema or frozen findings drift")
    expected_commands = [INSTALL_COMMAND, INSTALLED_COMMAND, PIP_CHECK_COMMAND]
    if evidence.get("requiredLinuxAcceptanceCommands") != expected_commands:
        failures.append("required Linux acceptance commands drift")
    gates = evidence.get("gates", {})
    if isinstance(gates, dict):
        expected_blockers = [
            gates[name]["blocker"]
            for name in EXPECTED_GATES
            if isinstance(gates.get(name), dict)
            and gates[name].get("status") == "blocked"
            and isinstance(gates[name].get("blocker"), str)
        ]
        if evidence.get("blockingReasons") != expected_blockers:
            failures.append("blockingReasons must exactly mirror blocked gates")
    failures.extend(validate_gates(evidence))
    return failures


def validate_licenses(evidence: dict) -> list[str]:
    failures: list[str] = []
    expected_paths = {item[4] for item in EXPECTED_ARTIFACTS.values()}
    license_root = ROOT / "third_party/licenses/python"
    actual_paths = {
        path.relative_to(ROOT).as_posix()
        for path in license_root.rglob("*")
        if path.is_file()
    }
    if actual_paths != expected_paths:
        failures.append("retained Python license file set drift")
    packages = evidence.get("packages", [])
    if not isinstance(packages, list):
        return failures
    for item in packages:
        if not isinstance(item, dict):
            continue
        relative = item.get("retainedLicensePath")
        if not isinstance(relative, str):
            continue
        path = ROOT / relative
        if not path.is_file():
            failures.append(f"retained license missing: {relative}")
        elif sha256(path) != item.get("wheelLicenseSha256"):
            failures.append(f"retained license does not byte-match inspected wheel: {relative}")
    return failures


def validate_sbom(sbom: dict) -> list[str]:
    failures: list[str] = []
    if set(sbom) != {
        "bomFormat",
        "specVersion",
        "serialNumber",
        "version",
        "metadata",
        "components",
        "dependencies",
    }:
        failures.append("SBOM top-level schema has missing or unexpected fields")
    if sbom.get("bomFormat") != "CycloneDX" or sbom.get("specVersion") != "1.6":
        failures.append("SBOM must be CycloneDX 1.6")
    if sbom.get("serialNumber") != "urn:uuid:4d19fdf7-2c78-5cee-b5c9-84a015384e87":
        failures.append("SBOM serial number drift")
    if sbom.get("version") != 1:
        failures.append("SBOM version must be 1")
    expected_metadata = {
        "timestamp": "2026-08-09T10:30:00Z",
        "component": {
            "type": "application",
            "bom-ref": "marketops-ai-workbench:m1-01-python-runtime@f9e81810e6e374e9047c36c53c480bcf6fbfd553",
            "name": "marketops-ai-workbench-m1-01-python-runtime",
            "version": "f9e81810e6e374e9047c36c53c480bcf6fbfd553",
        },
        "properties": [
            {"name": "marketops:scope", "value": "logical Python distribution SBOM"},
            {"name": "marketops:target", "value": "CPython 3.12.13; Linux x86_64; glibc >= 2.28"},
            {
                "name": "marketops:verification",
                "value": "artifact and metadata verified; clean Linux install and pip check blocked",
            },
        ],
    }
    if sbom.get("metadata") != expected_metadata:
        failures.append("SBOM metadata drift")
    components = sbom.get("components")
    if not isinstance(components, list):
        return failures + ["SBOM components must be an array"]
    by_name = {item.get("name"): item for item in components if isinstance(item, dict)}
    if [item.get("name") for item in components if isinstance(item, dict)] != list(
        EXPECTED_ARTIFACTS
    ):
        failures.append("SBOM component ordering drift")
    if len(by_name) != len(components) or set(by_name) != set(EXPECTED_ARTIFACTS):
        failures.append("SBOM component set must equal the frozen 15-package closure")
    for name, expected in EXPECTED_ARTIFACTS.items():
        item = by_name.get(name, {})
        if set(item) != {
            "type",
            "bom-ref",
            "name",
            "version",
            "purl",
            "hashes",
            "licenses",
            "externalReferences",
        }:
            failures.append(f"SBOM {name} component schema drift")
        expected_ref = f"pkg:pypi/{name}@{expected[0]}"
        if item.get("type") != "library" or item.get("version") != expected[0]:
            failures.append(f"SBOM {name} identity drift")
        if item.get("bom-ref") != expected_ref or item.get("purl") != expected_ref:
            failures.append(f"SBOM {name} purl drift")
        if item.get("hashes") != [{"alg": "SHA-256", "content": expected[2]}]:
            failures.append(f"SBOM {name} hash drift")
        if item.get("licenses") != [{"expression": expected[3]}]:
            failures.append(f"SBOM {name} license drift")
        refs = item.get("externalReferences")
        if refs != [{"type": "distribution", "url": expected[6]}]:
            failures.append(f"SBOM {name} distribution URL drift")
    dependency_items = sbom.get("dependencies")
    if not isinstance(dependency_items, list):
        return failures + ["SBOM dependencies must be an array"]
    for item in dependency_items:
        if not isinstance(item, dict) or set(item) != {"ref", "dependsOn"}:
            failures.append("SBOM dependency item schema drift")
    dependency_map = {item.get("ref"): item.get("dependsOn") for item in dependency_items if isinstance(item, dict)}
    root_ref = "marketops-ai-workbench:m1-01-python-runtime@f9e81810e6e374e9047c36c53c480bcf6fbfd553"
    expected_root = [f"pkg:pypi/{name}@{version}" for name, version in EXPECTED_DIRECT.items()]
    if dependency_map.get(root_ref) != expected_root:
        failures.append("SBOM root direct dependency set drift")
    for name, children in EXPECTED_GRAPH.items():
        ref = f"pkg:pypi/{name}@{EXPECTED_ARTIFACTS[name][0]}"
        expected_children = [f"pkg:pypi/{child}@{EXPECTED_ARTIFACTS[child][0]}" for child in children]
        if dependency_map.get(ref) != expected_children:
            failures.append(f"SBOM dependency edges drift for {name}")
    if set(dependency_map) != {root_ref} | {f"pkg:pypi/{name}@{data[0]}" for name, data in EXPECTED_ARTIFACTS.items()}:
        failures.append("SBOM contains missing or unexpected dependency nodes")
    return failures


def validate_notices(text: str) -> list[str]:
    failures: list[str] = []
    if text.count("## M1-01 Python runtime") != 1:
        failures.append("THIRD_PARTY_NOTICES must contain exactly one M1-01 Python runtime section")
    for name, expected in EXPECTED_ARTIFACTS.items():
        row = f"| {name} | {expected[0]} | {expected[3]} |"
        if text.count(row) != 1:
            failures.append(f"THIRD_PARTY_NOTICES row missing or duplicated for {name}")
    forbidden = {"colorama", "sniffio", "async-timeout", "exceptiongroup"}
    section = text.split("## M1-01 Python runtime", 1)[-1]
    for name in forbidden:
        if re.search(rf"\|\s*{re.escape(name)}\s*\|", section):
            failures.append(f"inactive package {name} appears in the runtime notice table")
    return failures


def mutation_self_test(
    evidence: dict, sbom: dict, lock_text: str, direct_text: str
) -> tuple[list[str], int]:
    missed: list[str] = []
    cases = []
    mutated = copy.deepcopy(evidence)
    mutated["packages"].pop()
    cases.append(("missing evidence package", validate_evidence(mutated)))
    mutated = copy.deepcopy(evidence)
    mutated["packages"][0]["sha256"] = "0" * 64
    cases.append(("artifact hash drift", validate_evidence(mutated)))
    mutated = copy.deepcopy(evidence)
    mutated["activeDependencyGraph"]["fastapi"] = []
    cases.append(("dependency edge removed", validate_evidence(mutated)))
    mutated = copy.deepcopy(evidence)
    mutated["gates"].pop("pipCheck")
    cases.append(
        ("required gate deleted", validate_gates(mutated), "gate names must be exactly")
    )
    mutated = copy.deepcopy(evidence)
    mutated["gates"]["pipCheck"] = {"status": "passed", "evidence": {}}
    cases.append(
        (
            "committed passed gate",
            validate_gates(mutated),
            "committed static evidence cannot self-attest passed gate pipCheck",
        )
    )
    mutated = copy.deepcopy(evidence)
    mutated["gates"]["pipCheck"] = {
        "status": "passed",
        "evidence": {
            "ciRunUrl": "https://github.com/Patch-A/marketops-ai-workbench/actions/runs/1",
            "headSha": evidence["baselineCommit"],
            "checkedAt": evidence["generatedAt"],
            "pythonVersion": "3.12.13",
            "platform": EXPECTED_GATE_PLATFORM,
            "command": PIP_CHECK_COMMAND,
            "resultSha256": hashlib.sha256(b"untrusted old-run result").hexdigest(),
        },
    }
    cases.append(
        (
            "old run URL and random result hash",
            validate_gates(mutated),
            "committed static evidence cannot self-attest passed gate pipCheck",
        )
    )
    mutated = copy.deepcopy(evidence)
    mutated["requiredLinuxAcceptanceCommands"][0] = (
        "python -m pip install --require-hashes --only-binary=:all: "
        "-r requirements/m1-01-runtime.txt"
    )
    cases.append(("unsafe install command", validate_evidence(mutated)))
    mutated = copy.deepcopy(evidence)
    mutated["runtime"]["acceptedWheelPlatforms"].pop()
    cases.append(("accepted platform removed", validate_evidence(mutated)))
    mutated = copy.deepcopy(evidence)
    mutated["artifactSelection"]["targetArguments"].pop()
    cases.append(("target argument removed", validate_evidence(mutated)))
    mutated = copy.deepcopy(evidence)
    mutated["packages"][0]["wheelLicensePath"] = "forged/LICENSE"
    cases.append(("wheel license path drift", validate_evidence(mutated)))
    mutated = copy.deepcopy(evidence)
    mutated["packages"][0]["size"] += 1
    cases.append(("wheel size drift", validate_evidence(mutated)))
    mutated = copy.deepcopy(evidence)
    mutated["excluded"][0]["reason"] = "forged reason"
    cases.append(("excluded reason drift", validate_evidence(mutated)))
    mutated = copy.deepcopy(evidence)
    mutated["wheelInspection"]["nativeWheelFindings"] = []
    cases.append(("native finding removed", validate_evidence(mutated)))
    mutated_sbom = copy.deepcopy(sbom)
    mutated_sbom["components"].pop()
    cases.append(("SBOM component removed", validate_sbom(mutated_sbom)))
    mutated_sbom = copy.deepcopy(sbom)
    mutated_sbom["metadata"]["properties"] = []
    cases.append(("SBOM metadata removed", validate_sbom(mutated_sbom)))
    cases.append(("sdist substituted", validate_lock(lock_text.replace(".whl", ".tar.gz", 1))[0]))
    cases.append(("extra requested", validate_direct(direct_text + "\nfastapi[standard]==0.141.1\n")))
    cases.append(("unexpected package", validate_lock(lock_text + "\nforged @ https://files.pythonhosted.org/packages/a/forged-1.0-py3-none-any.whl --hash=sha256:" + "0" * 64 + "\n")[0]))
    cases.append(("inactive package mandatory", validate_lock(lock_text + "\ncolorama @ https://files.pythonhosted.org/packages/a/colorama-0.4.6-py2.py3-none-any.whl --hash=sha256:" + "0" * 64 + "\n")[0]))
    for case in cases:
        name, result = case[0:2]
        expected_failure = case[2] if len(case) == 3 else None
        if not result:
            missed.append(f"mutation self-test missed: {name}")
        elif expected_failure is not None and not any(
            expected_failure in failure for failure in result
        ):
            missed.append(
                f"mutation self-test caught {name} for the wrong reason; "
                f"expected {expected_failure!r}"
            )
    return missed, len(cases)


def validate_installed_environment() -> tuple[list[str], str | None]:
    failures: list[str] = []
    in_virtual_environment = sys.prefix != sys.base_prefix
    if not in_virtual_environment:
        failures.append("installed mode requires a fresh disposable virtual environment")
    if platform.python_implementation() != "CPython":
        failures.append("installed mode requires CPython")
    if sys.version_info[:3] != (3, 12, 13):
        failures.append("installed mode requires Python 3.12.13")
    if sys.platform != "linux":
        failures.append("installed mode requires Linux")
    if platform.machine().lower() not in {"x86_64", "amd64"}:
        failures.append("installed mode requires x86_64")
    libc_name, libc_version = platform.libc_ver()
    if libc_name.lower() != "glibc":
        failures.append("installed mode requires glibc")
    else:
        try:
            glibc_parts = tuple(int(part) for part in libc_version.split(".")[:2])
        except ValueError:
            glibc_parts = ()
        if glibc_parts < (2, 28):
            failures.append("installed mode requires glibc >= 2.28")

    installed_versions: dict[str, str] = {}
    imported_modules: list[str] = []
    for name, expected in EXPECTED_ARTIFACTS.items():
        try:
            installed = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            failures.append(f"installed distribution missing: {name}")
            continue
        installed_versions[name] = installed
        if installed != expected[0]:
            failures.append(
                f"installed version drift for {name}: expected {expected[0]}, got {installed}"
            )
        module_name = EXPECTED_IMPORTS[name]
        try:
            importlib.import_module(module_name)
        except Exception as error:
            failures.append(f"installed import failed for {module_name}: {type(error).__name__}")
        else:
            imported_modules.append(module_name)

    installed_distributions = sorted(
        {
            re.sub(r"[-_.]+", "-", distribution.metadata["Name"]).lower()
            for distribution in importlib.metadata.distributions()
            if distribution.metadata.get("Name")
        }
    )
    unexpected_distributions = sorted(
        set(installed_distributions) - set(EXPECTED_ARTIFACTS) - {"pip"}
    )
    if unexpected_distributions:
        failures.append(
            "installed environment contains unexpected distributions: "
            + ", ".join(unexpected_distributions)
        )

    try:
        pip_version = importlib.metadata.version("pip")
    except importlib.metadata.PackageNotFoundError:
        pip_version = "missing"
    if pip_version != "26.0.1":
        failures.append(f"installed pip version must be 26.0.1, got {pip_version}")

    pip_check = subprocess.run(
        [sys.executable, "-m", "pip", "check"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    if pip_check.returncode != 0:
        failures.append("pip check failed in installed environment")

    if failures:
        return failures, None
    result = {
        "command": INSTALLED_COMMAND,
        "lockSha256": sha256(LOCK_PATH),
        "pythonVersion": platform.python_version(),
        "implementation": platform.python_implementation(),
        "platform": EXPECTED_GATE_PLATFORM,
        "machine": platform.machine(),
        "libc": f"{libc_name}-{libc_version}",
        "pipVersion": pip_version,
        "environmentRequirement": FRESH_ENVIRONMENT_REQUIREMENT,
        "virtualEnvironment": in_virtual_environment,
        "installCommand": INSTALL_COMMAND,
        "installedDistributions": installed_distributions,
        "installedVersions": installed_versions,
        "importedModules": sorted(imported_modules),
        "pipCheckCommand": PIP_CHECK_COMMAND,
        "pipCheckOutput": pip_check.stdout.strip(),
    }
    digest = hashlib.sha256(
        json.dumps(result, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return [], digest


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Validate the M1-01 Python runtime lock without overstating installation evidence."
    )
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument(
        "--static",
        action="store_true",
        help="validate committed lock evidence; blocked live-install gates are allowed",
    )
    modes.add_argument(
        "--installed",
        action="store_true",
        help="also validate the current Linux installation, imports, and pip check",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    failures: list[str] = []
    required_paths = [EVIDENCE_PATH, LOCK_PATH, DIRECT_PATH, SBOM_PATH, NOTICES_PATH, ADMISSION_PATH, DECISION_PATH, ROOT / ".python-version"]
    missing = [str(path.relative_to(ROOT)) for path in required_paths if not path.is_file()]
    if missing:
        print("Missing required files: " + ", ".join(missing), file=sys.stderr)
        return 1

    evidence = load_json(EVIDENCE_PATH, failures)
    sbom = load_json(SBOM_PATH, failures)
    lock_text = LOCK_PATH.read_text(encoding="utf-8")
    direct_text = DIRECT_PATH.read_text(encoding="utf-8")

    if (ROOT / ".python-version").read_text(encoding="utf-8").strip() != "3.12.13":
        failures.append(".python-version must be exactly 3.12.13")
    if sha256(ADMISSION_PATH) != EXPECTED_ADMISSION_SHA256:
        failures.append("runtime dependency admission input has drifted")
    if sha256(DECISION_PATH) != EXPECTED_DECISION_SHA256:
        failures.append("runtime dependency decision input has drifted")
    failures.extend(validate_direct(direct_text))
    lock_failures, _ = validate_lock(lock_text)
    failures.extend(lock_failures)
    failures.extend(validate_evidence(evidence))
    failures.extend(validate_licenses(evidence))
    failures.extend(validate_sbom(sbom))
    failures.extend(validate_notices(NOTICES_PATH.read_text(encoding="utf-8")))
    mutation_count = 0
    if not failures:
        mutation_failures, mutation_count = mutation_self_test(
            evidence, sbom, lock_text, direct_text
        )
        failures.extend(mutation_failures)

    if failures:
        print("M1-01 Python runtime lock validation failed:", file=sys.stderr)
        for failure in failures:
            print(f"- {failure}", file=sys.stderr)
        return 1

    if args.installed:
        installed_failures, result_sha256 = validate_installed_environment()
        if installed_failures:
            print("M1-01 installed Python runtime validation failed:", file=sys.stderr)
            for failure in installed_failures:
                print(f"- {failure}", file=sys.stderr)
            return 1
        print(
            "M1-01 installed Python runtime passed: exact versions, imports, and pip check."
        )
        print(f"INSTALLED_RESULT_SHA256={result_sha256}")
        return 0

    blocked_count = sum(
        1
        for gate in evidence["gates"].values()
        if gate.get("status") == "blocked"
    )
    print(
        "M1-01 Python runtime static checks passed: 15 exact wheels, "
        f"15 retained licenses, CycloneDX metadata, {len(EXPECTED_GATES)} exact gates, "
        f"and {mutation_count} weakening mutations."
    )
    print(
        f"STATIC ONLY: {blocked_count} gate(s) remain blocked; "
        "this does not validate a Linux installation, imports, or pip check."
    )
    if blocked_count and not args.static:
        print(
            "M1-01 Python runtime acceptance remains blocked; use --static only "
            "for the explicitly non-acceptance static audit.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
