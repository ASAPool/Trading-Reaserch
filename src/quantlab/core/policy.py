"""Loading, fingerprinting and verifying the data-quality policy (DQP-1).

Fingerprints are SHA-256 of the file bytes with line endings normalised to
LF, so the same policy has the same fingerprint on Windows and Linux.

The registry (config/policy_registry.toml) records, per version, the commit
hash that contains the policy and its fingerprint. The registry is added in a
LATER commit than the policy itself, because a file cannot contain the hash of
the commit that creates it.
"""

import hashlib
import subprocess
import tomllib
from dataclasses import dataclass
from pathlib import Path


class PolicyError(Exception):
    """The policy is missing, malformed, unregistered or has been changed."""


def canonical_sha256(path: Path) -> str:
    """SHA-256 of a text file with CRLF/CR line endings normalised to LF."""
    data = Path(path).read_bytes().replace(b"\r\n", b"\n").replace(b"\r", b"\n")
    return hashlib.sha256(data).hexdigest()


@dataclass(frozen=True)
class SensitivityRule:
    floor: float
    relative: float


@dataclass(frozen=True)
class DataQualityPolicy:
    version: str
    zero_tolerance: float
    variant_b_masked_categories: tuple
    total_return: SensitivityRule
    expectancy: SensitivityRule
    max_drawdown: SensitivityRule
    trade_count: SensitivityRule
    gap_contribution: SensitivityRule
    gap_expectancy: SensitivityRule
    raw: dict

    @classmethod
    def from_dict(cls, d: dict) -> "DataQualityPolicy":
        try:
            s, g = d["sensitivity"], d["gap_diagnostic"]
            policy = cls(
                version=d["policy"]["version"],
                zero_tolerance=float(d["numerics"]["zero_tolerance"]),
                variant_b_masked_categories=tuple(d["variants"]["variant_b_masked_categories"]),
                total_return=SensitivityRule(float(s["total_return_floor"]), float(s["total_return_relative"])),
                expectancy=SensitivityRule(float(s["expectancy_floor"]), float(s["expectancy_relative"])),
                max_drawdown=SensitivityRule(float(s["max_drawdown_floor"]), float(s["max_drawdown_relative"])),
                trade_count=SensitivityRule(float(s["trade_count_floor"]), float(s["trade_count_relative"])),
                gap_contribution=SensitivityRule(float(g["contribution_floor"]), float(g["contribution_relative"])),
                gap_expectancy=SensitivityRule(float(g["expectancy_floor"]), float(g["expectancy_relative"])),
                raw=d,
            )
        except (KeyError, TypeError, ValueError) as e:
            raise PolicyError(f"policy file is incomplete or malformed: {e!r}") from None
        if d["missing_bars"].get("fill") != "never":
            raise PolicyError("policy must never fill missing bars")
        if d["execution_after_gap"].get("use_second_provider_prices") is not False:
            raise PolicyError("policy must never use second-provider prices for execution")
        if d["variants"].get("official") != "A":
            raise PolicyError("Variant A must be the official result")
        if not (0 < policy.zero_tolerance < 1e-6):
            raise PolicyError("zero_tolerance must be a tiny positive number (rounding noise only)")
        return policy


@dataclass(frozen=True)
class PolicyIdentity:
    """What every experiment must record about the policy it used."""
    version: str
    commit: str
    sha256: str

    def as_dict(self) -> dict:
        return {"policy_version": self.version, "policy_commit": self.commit,
                "policy_sha256": self.sha256}


def load_policy(policy_path: Path) -> DataQualityPolicy:
    with open(policy_path, "rb") as f:
        return DataQualityPolicy.from_dict(tomllib.load(f))


def verify_registered(policy_path: Path, registry_path: Path) -> PolicyIdentity:
    """Confirm the policy file is exactly the registered version. Raise otherwise."""
    policy = load_policy(policy_path)
    if not Path(registry_path).exists():
        raise PolicyError(f"policy registry {registry_path} not found; the policy is not registered")
    with open(registry_path, "rb") as f:
        registry = tomllib.load(f)
    entry = registry.get(policy.version)
    if entry is None:
        raise PolicyError(f"policy version {policy.version} is not in the registry")
    actual = canonical_sha256(policy_path)
    if actual != entry.get("sha256"):
        raise PolicyError(f"policy file fingerprint {actual} does not match the registered "
                          f"{entry.get('sha256')} for {policy.version}: the policy was changed")
    commit = entry.get("commit", "")
    if len(commit) != 40 or any(c not in "0123456789abcdef" for c in commit):
        raise PolicyError(f"registered commit {commit!r} is not a full git commit hash")
    return PolicyIdentity(policy.version, commit, actual)


def policy_content_at_commit(repo_dir: Path, commit: str, rel_path: str):
    """Return the policy file's canonical SHA-256 as stored in `commit`, or None
    if git (or that commit) is unavailable."""
    try:
        out = subprocess.run(["git", "show", f"{commit}:{rel_path}"], cwd=repo_dir,
                             capture_output=True, timeout=20)
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0:
        return None
    data = out.stdout.replace(b"\r\n", b"\n").replace(b"\r", b"\n")
    return hashlib.sha256(data).hexdigest()


def current_code_commit(repo_dir: Path):
    try:
        out = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo_dir,
                             capture_output=True, text=True, timeout=20)
        return out.stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        return None
