# Third-Party Notices

This repository is licensed under Apache-2.0. Third-party projects retain their own licenses.

## Current exploratory prototype

- [Lucide](https://github.com/lucide-icons/lucide), ISC License. The static prototype loads the UMD bundle from unpkg and does not vendor the library in this repository.

## Local development tools not redistributed

- [mouse-lin/finesse-skill](https://github.com/mouse-lin/finesse-skill), MIT License. A local adapted copy is excluded from the public repository pending a complete review of bundled examples and third-party libraries.

Candidate components listed in `docs/OPEN_SOURCE_REVIEW.md` are not dependencies until they are added to a package manifest or vendored with their required notices.

## M1-01 Python runtime

The following exact Python distributions are present in the M1-01 hashed runtime manifest. Their selected-wheel license texts are retained under `third_party/licenses/python/`. This table does not state that the lock has passed its clean-Linux installation gate.

| Distribution | Version | License |
| --- | --- | --- |
| annotated-doc | 0.0.4 | MIT |
| annotated-types | 0.7.0 | MIT |
| anyio | 4.12.1 | MIT |
| asyncpg | 0.31.0 | Apache-2.0 |
| click | 8.2.1 | BSD-3-Clause |
| fastapi | 0.141.1 | MIT |
| h11 | 0.16.0 | MIT |
| idna | 3.18 | BSD-3-Clause |
| pydantic | 2.13.4 | MIT |
| pydantic-core | 2.46.4 | MIT |
| python-multipart | 0.0.32 | Apache-2.0 |
| starlette | 1.6.0 | BSD-3-Clause |
| typing-extensions | 4.16.0 | PSF-2.0 |
| typing-inspection | 0.4.2 | MIT |
| uvicorn | 0.52.1 | BSD-3-Clause |

No selected wheel contained a separate NOTICE entry. The `asyncpg` and `pydantic-core` Linux wheels contain native extension modules; their wheel license entries are retained here, while a source-level inventory of statically linked compiler dependencies remains outside this logical Python-distribution SBOM.
