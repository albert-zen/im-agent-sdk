from __future__ import annotations

import argparse
import hashlib
import re
import tomllib
import zipfile
from email.parser import Parser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FULL_COMMIT_PATTERN = re.compile(r"[0-9a-f]{40}")
AUTHORIZED_VERSION = "0.1.0a1"
AUTHORIZED_REPOSITORY = "albert-zen/im-agent-sdk"


def prepare_release(
    *,
    wheel: Path,
    tag: str,
    source_commit: str,
    repository: str,
    output_directory: Path,
) -> tuple[Path, Path]:
    metadata = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    project = metadata["project"]
    name = project["name"]
    version = project["version"]
    if version != AUTHORIZED_VERSION:
        raise ValueError(
            f"package version {version!r} is not the authorized release {AUTHORIZED_VERSION!r}"
        )
    expected_tag = f"v{version}"
    expected_wheel_name = f"{name.replace('-', '_')}-{version}-py3-none-any.whl"

    if tag != expected_tag:
        raise ValueError(f"tag {tag!r} does not match package version tag {expected_tag!r}")
    if FULL_COMMIT_PATTERN.fullmatch(source_commit) is None:
        raise ValueError("source commit must be a lowercase full 40-character Git commit")
    if repository != AUTHORIZED_REPOSITORY:
        raise ValueError(
            f"repository {repository!r} is not the authorized publication repository "
            f"{AUTHORIZED_REPOSITORY!r}; refusing to prepare release evidence for it"
        )
    if repository.count("/") != 1 or any(part == "" for part in repository.split("/")):
        raise ValueError("repository must use owner/name form")
    if wheel.name != expected_wheel_name:
        raise ValueError(f"wheel filename {wheel.name!r} does not match {expected_wheel_name!r}")
    if not wheel.is_file():
        raise ValueError(f"wheel does not exist: {wheel}")

    with zipfile.ZipFile(wheel) as archive:
        names = archive.namelist()
        metadata_paths = [name for name in names if name.endswith(".dist-info/METADATA")]
        if len(metadata_paths) != 1:
            raise ValueError("wheel must contain exactly one dist-info/METADATA file")
        wheel_metadata = Parser().parsestr(
            archive.read(metadata_paths[0]).decode("utf-8"), headersonly=True
        )
        if wheel_metadata["Name"] != name:
            raise ValueError("wheel project name does not match pyproject.toml")
        if wheel_metadata["Version"] != version:
            raise ValueError("wheel version does not match pyproject.toml")
        required_members = {
            "imagent/py.typed",
            "examples/reference_consumer/main.py",
        }
        missing_members = required_members.difference(names)
        if missing_members:
            raise ValueError(f"wheel is missing required members: {sorted(missing_members)!r}")

    digest = hashlib.sha256(wheel.read_bytes()).hexdigest()
    output_directory.mkdir(parents=True, exist_ok=True)
    checksum_path = output_directory / "SHA256SUMS"
    checksum_path.write_text(f"{digest}  {wheel.name}\n", encoding="ascii")

    asset_url = f"https://github.com/{repository}/releases/download/{tag}/{wheel.name}"
    notes_path = output_directory / "RELEASE_NOTES.md"
    notes_path.write_text(
        "\n".join(
            (
                f"# IM Agent SDK {version}",
                "",
                "This is an alpha GitHub prerelease. It is not published to PyPI.",
                "",
                f"- Source commit: `{source_commit}`",
                f"- Wheel: `{wheel.name}`",
                f"- SHA-256: `{digest}`",
                f"- Stable asset URL: {asset_url}",
                "",
                "The repository is private, so downloading the asset requires an authenticated",
                "GitHub session with repository read access. Verify `SHA256SUMS` before install.",
                "",
            )
        ),
        encoding="utf-8",
    )
    return checksum_path, notes_path


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Validate one SDK wheel and prepare GitHub Release evidence."
    )
    parser.add_argument("--wheel", type=Path, required=True)
    parser.add_argument("--tag", required=True)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--output-directory", type=Path, required=True)
    arguments = parser.parse_args()
    checksum_path, notes_path = prepare_release(
        wheel=arguments.wheel,
        tag=arguments.tag,
        source_commit=arguments.source_commit,
        repository=arguments.repository,
        output_directory=arguments.output_directory,
    )
    print(checksum_path)
    print(notes_path)


if __name__ == "__main__":
    main()
