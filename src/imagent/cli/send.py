from __future__ import annotations

import argparse
import base64
import hashlib
import json
import mimetypes
import sys
from pathlib import Path
from urllib.parse import urlparse

import httpx


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="imagent-send",
        description="Submit proactive text and artifacts to a consumer-hosted local SDK ingress.",
    )
    parser.add_argument("--endpoint", required=True)
    parser.add_argument("--credential-file", type=Path)
    parser.add_argument(
        "--credential-stdin",
        action="store_true",
        help="read the scoped delivery credential from standard input",
    )
    parser.add_argument("--delivery-id", required=True)
    parser.add_argument("--text")
    parser.add_argument("--markdown", action="store_true")
    parser.add_argument("--artifact", action="append", type=Path, default=[])
    parser.add_argument("--application")
    parser.add_argument("--thread")
    parser.add_argument("--project")
    parser.add_argument("--route")
    parser.add_argument("--channel")
    parser.add_argument("--conversation")
    parser.add_argument("--timeout", type=float, default=60.0)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    arguments = parser.parse_args(argv)
    try:
        endpoint = _loopback_endpoint(arguments.endpoint)
        credential = _read_credential(arguments)
        target = _target(arguments)
        content = _content(arguments)
        if not content:
            parser.error("provide --text and/or at least one --artifact")
        with httpx.Client(trust_env=False) as client:
            response = client.post(
                endpoint,
                headers={
                    "Authorization": f"Bearer {credential}",
                    "Content-Type": "application/json",
                },
                json={
                    "deliveryId": arguments.delivery_id,
                    "target": target,
                    "content": content,
                },
                timeout=arguments.timeout,
            )
    except (OSError, ValueError, httpx.HTTPError) as error:
        print(f"imagent-send: {error}", file=sys.stderr)
        return 2

    try:
        body = response.json()
    except ValueError:
        print(
            f"imagent-send: ingress returned non-JSON HTTP {response.status_code}",
            file=sys.stderr,
        )
        return 2
    print(json.dumps(body, ensure_ascii=False, indent=2))
    if response.status_code >= 400:
        return 2
    state = body.get("state") if isinstance(body, dict) else None
    return 0 if state == "accepted" else 3


def _loopback_endpoint(value: str) -> str:
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("endpoint must use http or https")
    if parsed.hostname not in {"127.0.0.1", "::1"}:
        raise ValueError("reference CLI accepts loopback endpoints only")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("endpoint must not contain credentials")
    return value


def _read_credential(arguments: argparse.Namespace) -> str:
    if bool(arguments.credential_file) == bool(arguments.credential_stdin):
        raise ValueError("choose exactly one of --credential-file or --credential-stdin")
    if arguments.credential_stdin:
        credential = sys.stdin.readline().strip()
    else:
        credential = arguments.credential_file.read_text(encoding="utf-8").strip()
    if not credential:
        raise ValueError("delivery credential is empty")
    return credential


def _target(arguments: argparse.Namespace) -> dict[str, object]:
    thread_target = bool(arguments.application or arguments.thread or arguments.project)
    conversation_target = bool(arguments.channel or arguments.conversation)
    if thread_target == conversation_target:
        raise ValueError("choose either --application/--thread or --channel/--conversation")
    if thread_target:
        if not arguments.application or not arguments.thread:
            raise ValueError("--application and --thread are both required")
        target: dict[str, object] = {
            "kind": "threadRoutes",
            "applicationInstanceId": arguments.application,
            "nativeThreadId": arguments.thread,
        }
        if arguments.project:
            target["projectRef"] = {
                "applicationInstanceId": arguments.application,
                "nativeProjectId": arguments.project,
            }
        if arguments.route:
            target["routeId"] = arguments.route
        return target
    if not arguments.channel or not arguments.conversation:
        raise ValueError("--channel and --conversation are both required")
    if arguments.route:
        raise ValueError("--route applies only to a Thread target")
    return {
        "kind": "conversation",
        "channelInstanceId": arguments.channel,
        "nativeConversationId": arguments.conversation,
    }


def _content(arguments: argparse.Namespace) -> list[dict[str, object]]:
    content: list[dict[str, object]] = []
    if arguments.text is not None:
        content.append(
            {
                "type": "text",
                "text": arguments.text,
                "format": "markdown" if arguments.markdown else "plain",
            }
        )
    elif arguments.markdown:
        raise ValueError("--markdown requires --text")
    for index, path in enumerate(arguments.artifact, start=1):
        resolved = path.resolve(strict=True)
        if not resolved.is_file():
            raise ValueError(f"artifact is not a regular file: {path}")
        raw = resolved.read_bytes()
        digest = hashlib.sha256(raw).hexdigest()
        media_type, _encoding = mimetypes.guess_type(resolved.name)
        content.append(
            {
                "type": "inlineArtifact",
                "attachmentId": f"artifact-{index}-{digest[:16]}",
                "filename": resolved.name,
                "mediaType": media_type or "application/octet-stream",
                "sizeBytes": len(raw),
                "contentBase64": base64.b64encode(raw).decode("ascii"),
            }
        )
    return content


if __name__ == "__main__":
    raise SystemExit(main())
