# Changelog

## 0.4.0 — release candidate

This establishes the first coherent public release-note baseline; it does not reconstruct every
internal milestone. The v1 feature scope is frozen at product version 0.4.0.

### Added

- Public baseline of ten Built-ins and 282 production cases, including bounded synthetic Reactive
  execution, failure recovery, and partially observable information acquisition/restraint.
- Reproducible artifacts, durable attempt evidence, offline scoring/replay and two-run comparison.
- Offline `elarabench list` discovery, public installation instructions and a CLI Quickstart.
- Apache-2.0 framework license; bundled benchmark data retain their existing CC0-1.0 treatment.

### Changed

- Product/package version advances from 0.2.1 to 0.4.0 with Alpha metadata and version checks.
- User-first README, concise CLI help and missing-suite discovery guidance.
- Wheel and sdist verification covers version, licenses and installed CLI use.
- Ollama is the implemented production provider; other adapters remain future work.

### Compatibility

- Release hardening changes no benchmark semantics, corpus bytes or the ten production hashes.
- Reactive evaluator 1.3.0, latest physical schema v4, latest Summary v9 and fingerprint v3 remain
  unchanged. Historical artifacts may use earlier supported schemas.
- Historical offline processing follows eligibility rules. Resume requires compatible runtime
  provenance; scoring upgrades derived evaluations without rewriting canonical evidence.
- This CLI-first pre-1.0 release does not promise broad Python API or indefinite artifact compatibility.
