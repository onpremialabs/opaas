"""OPAAS — On-Prem AI Audit Suite (onpremIA Labs)."""

__version__ = "0.4.0"

# Version of the evaluation methodology. It is embedded in every report and in
# the credential (`evalRun.opaasVersion`); it changes only when the
# methodology changes, not on every code release.
OPAAS_SPEC_VERSION = "1.0-draft"

__all__ = ["__version__", "OPAAS_SPEC_VERSION"]
