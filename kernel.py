#!/usr/bin/env python3
"""Cortex runtime: observable inputs, bounded computation, durable evidence.

This is a deterministic data-processing pipeline, not a trained deep-learning
model. Network adapters report their real outcomes and never label a mock as a
successful protocol operation.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import socket
import ssl
import struct
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
SYNAPSES = ROOT / "synapses"
STATE_FILE = SYNAPSES / "state.json"
WEIGHTS_FILE = SYNAPSES / "cortex_weights.json"
LEDGER_FILE = SYNAPSES / "cortex_ledger.json"
PEERS_FILE = SYNAPSES / "peers.json"
CONTRACT_FILE = ROOT / "contract.json"
README_FILE = ROOT / "README.md"
USER_AGENT = "Cortex-Runtime/1.0"
HTTP_TIMEOUT = float(os.getenv("CORTEX_HTTP_TIMEOUT", "8"))
MAX_PEERS = int(os.getenv("CORTEX_MAX_PEERS", "10"))


def read_json(path: Path, default: Any) -> Any:
    try:
        with path.open("r", encoding="utf-8") as stream:
            return json.load(stream)
    except FileNotFoundError:
        return default


def atomic_json_write(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as stream:
        json.dump(data, stream, ensure_ascii=False, indent=2, sort_keys=True)
        stream.write("\n")
    temporary.replace(path)


def fetch_json(url: str, token: str | None = None) -> Any:
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT) as response:
        if response.status != 200:
            raise RuntimeError(f"HTTP status {response.status}")
        return json.loads(response.read().decode("utf-8"))


def peer_url(item: Any) -> str | None:
    """Accept a URL or 'owner/repo' entry from synapses/peers.json."""
    if isinstance(item, str) and item.startswith(("https://", "http://")):
        return item
    if isinstance(item, str) and "/" in item:
        owner, repo = item.strip("/").split("/", 1)
        if owner and repo and all(part not in (".", "..") for part in (owner, repo)):
            return (
                "https://raw.githubusercontent.com/"
                f"{owner}/{repo}/HEAD/synapse_forward.json"
            )
    if isinstance(item, dict):
        url = item.get("url")
        if isinstance(url, str) and url.startswith(("https://", "http://")):
            return url
        owner, repo = item.get("owner"), item.get("repo")
        filename = item.get("filename", "synapse_forward.json")
        if isinstance(owner, str) and isinstance(repo, str):
            safe_name = urllib.parse.quote(str(filename), safe="/")
            return (
                "https://raw.githubusercontent.com/"
                f"{owner}/{repo}/HEAD/{safe_name}"
            )
    return None


def collect_inputs() -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    """Read the local contract and attempt configured peer fetches."""
    observations: list[dict[str, Any]] = []
    events: list[dict[str, str]] = []

    if CONTRACT_FILE.is_file():
        raw = CONTRACT_FILE.read_bytes()
        try:
            parsed = json.loads(raw.decode("utf-8"))
            valid_json = True
        except (UnicodeDecodeError, json.JSONDecodeError):
            parsed = None
            valid_json = False
        observations.append({
            "source": "local:contract.json",
            "kind": "local_contract",
            "sha256": hashlib.sha256(raw).hexdigest(),
            "bytes": len(raw),
            "valid_json": valid_json,
            "payload": parsed if isinstance(parsed, (dict, list)) else None,
        })
        events.append({"source": "local:contract.json", "status": "success"})
    else:
        events.append({"source": "local:contract.json", "status": "missing"})

    peers_data = read_json(PEERS_FILE, {"mesh_nodes": []})
    peer_items = peers_data.get("mesh_nodes", []) if isinstance(
        peers_data, dict
    ) else []
    if not isinstance(peer_items, list):
        peer_items = []

    for item in peer_items[:MAX_PEERS]:
        url = peer_url(item)
        label = str(item)[:160]
        if not url:
            events.append({"source": label, "status": "invalid_peer_config"})
            continue
        try:
            payload = fetch_json(url)
            encoded = json.dumps(
                payload, ensure_ascii=False, sort_keys=True
            ).encode("utf-8")
            observations.append({
                "source": url,
                "kind": "remote_peer",
                "sha256": hashlib.sha256(encoded).hexdigest(),
                "bytes": len(encoded),
                "valid_json": True,
                "payload": payload,
            })
            events.append({"source": url, "status": "success"})
        except Exception as exc:
            events.append({
                "source": url,
                "status": "error",
                "detail": f"{type(exc).__name__}: {exc}"[:300],
            })

    release_repo = os.getenv("CORTEX_RELEASE_REPO", "").strip()
    if release_repo and "/" in release_repo:
        url = f"https://api.github.com/repos/{release_repo}/releases/latest"
        try:
            release = fetch_json(url, os.getenv("GITHUB_TOKEN"))
            encoded = json.dumps(
                release, ensure_ascii=False, sort_keys=True
            ).encode("utf-8")
            observations.append({
                "source": url,
                "kind": "github_release",
                "sha256": hashlib.sha256(encoded).hexdigest(),
                "bytes": len(encoded),
                "valid_json": True,
                "payload": {
                    "tag_name": release.get("tag_name"),
                    "published_at": release.get("published_at"),
                    "html_url": release.get("html_url"),
                },
            })
            events.append({"source": url, "status": "success"})
        except Exception as exc:
            events.append({
                "source": url,
                "status": "error",
                "detail": f"{type(exc).__name__}: {exc}"[:300],
            })

    return observations, events


def sigmoid(value: float) -> float:
    value = max(-60.0, min(60.0, value))
    return 1.0 / (1.0 + math.exp(-value))


def feature_vector(observations: list[dict[str, Any]]) -> list[float]:
    """Derive bounded, reproducible features from observed payloads."""
    if not observations:
        raise RuntimeError(
            "No real input was collected; refusing to fabricate input features."
        )
    total_bytes = sum(max(0, int(item.get("bytes", 0))) for item in observations)
    valid_count = sum(bool(item.get("valid_json")) for item in observations)
    remote_count = sum(item.get("kind") == "remote_peer" for item in observations)
    release_count = sum(item.get("kind") == "github_release" for item in observations)
    source_count = len(observations)
    byte_feature = min(1.0, math.log1p(total_bytes) / 20.0)
    return [
        min(1.0, valid_count / source_count),
        min(1.0, remote_count / max(1, source_count)),
        min(1.0, release_count / max(1, source_count)),
        byte_feature,
    ]


def compute_pipeline(
    features: list[float], weights: list[float]
) -> dict[str, Any]:
    """Five named deterministic stages; not a neural network training loop."""
    stage_1 = features
    stage_2 = [round(float(value), 8) for value in stage_1]
    products = [
        value * weights[index % len(weights)]
        for index, value in enumerate(stage_2)
    ]
    stage_3 = [max(0.0, value) for value in products]  # ReLU
    # A normalized weighted aggregation, not transformer attention.
    denominator = sum(abs(value) for value in weights) or 1.0
    stage_4 = sum(
        activation * abs(weights[index % len(weights)])
        for index, activation in enumerate(stage_3)
    ) / denominator
    stage_5 = sigmoid(stage_4)
    return {
        "input": stage_1,
        "embedding_features": stage_2,
        "hidden_activations": [round(value, 8) for value in stage_3],
        "symbolic_score": round(stage_4, 8),
        "output_score": round(stage_5, 8),
        "pipeline_label": "deterministic_feature_pipeline_not_trained_deep_learning",
    }


def mqtt_publish(
    host: str, port: int, topic: str, message: bytes, timeout: float = 5.0
) -> None:
    """Publish one MQTT 3.1.1 QoS 0 message using a direct TCP/TLS socket."""
    client_id = f"cortex-{os.getpid()}"[: client_id_limit()]
    remaining = 10 + len(client_id.encode("utf-8"))
    connect_body = (
        b"\x00\x04MQTT"  # Protocol name
        b"\x04"           # MQTT 3.1.1
        b"\x02"           # Clean session
        b"\x00\x1e"       # Keepalive 30 seconds
        + struct.pack("!H", len(client_id.encode("utf-8")))
        + client_id.encode("utf-8")
    )
    packet = b"\x10" + encode_remaining_length(len(connect_body)) + connect_body
    with socket.create_connection((host, port), timeout=timeout) as raw_socket:
        if port == 8883:
            context = ssl.create_default_context()
            connection = context.wrap_socket(raw_socket, server_hostname=host)
        else:
            connection = raw_socket
        connection.settimeout(timeout)
        connection.sendall(packet)
        response = connection.recv(4)
        if len(response) < 4 or response[0:2] != b"\x20\x02" or response[3] != 0:
            raise RuntimeError(f"MQTT CONNECT rejected: {response!r}")
        topic_bytes = topic.encode("utf-8")
        body = struct.pack("!H", len(topic_bytes)) + topic_bytes + message
        publish_packet = b"\x30" + encode_remaining_length(len(body)) + body
        connection.sendall(publish_packet)
        connection.sendall(b"\xe0\x00")  # DISCONNECT


def client_id_limit() -> int:
    return 64


def encode_remaining_length(length: int) -> bytes:
    encoded = bytearray()
    while True:
        digit = length % 128
        length //= 128
        if length > 0:
            digit |= 0x80
        encoded.append(digit)
        if length == 0:
            return bytes(encoded)


def attempt_mqtt_publish(packet: dict[str, Any]) -> dict[str, Any]:
    host = os.getenv("CORTEX_MQTT_HOST", "").strip()
    topic = os.getenv("CORTEX_MQTT_TOPIC", "cortex/events").strip()
    if not host:
        return {"status": "not_configured"}
    port = int(os.getenv("CORTEX_MQTT_PORT", "8883"))
    username = os.getenv("CORTEX_MQTT_USERNAME")
    password = os.getenv("CORTEX_MQTT_PASSWORD")
    if username or password:
        return {
            "status": "unsupported_auth_configuration",
            "detail": "This minimal adapter does not implement MQTT username/password.",
        }
    try:
        mqtt_publish(
            host, port, topic,
            json.dumps(packet, sort_keys=True).encode("utf-8"),
        )
        return {"status": "success", "protocol": "MQTT 3.1.1 QoS 0"}
    except Exception as exc:
        return {
            "status": "error",
            "detail": f"{type(exc).__name__}: {exc}"[:300],
        }


def send_github_dispatch(packet: dict[str, Any]) -> dict[str, Any]:
    token = os.getenv("GITHUB_TOKEN")
    target = os.getenv("CORTEX_DISPATCH_REPOSITORY", "").strip()
    event_type = os.getenv("CORTEX_DISPATCH_EVENT", "").strip()
    if not target or not event_type:
        return {"status": "not_configured"}
    if not token:
        return {"status": "missing_token"}
    if "/" not in target:
        return {"status": "invalid_repository"}
    url = f"https://api.github.com/repos/{target}/dispatches"
    body = json.dumps({
        "event_type": event_type,
        "client_payload": {
            "sender": "cortex",
            "epoch": packet.get("epoch"),
            "evidence_hash": packet.get("evidence_hash"),
        },
    }).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "Content-Type": "application/json",
            "User-Agent": USER_AGENT,
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT) as response:
            if response.status == 204:
                return {"status": "success", "http_status": response.status}
            return {"status": "error", "http_status": response.status}
    except urllib.error.HTTPError as exc:
        return {"status": "error", "http_status": exc.code, "detail": str(exc)[:300]}
    except Exception as exc:
        return {"status": "error", "detail": f"{type(exc).__name__}: {exc}"[:300]}


def main() -> int:
    SYNAPSES.mkdir(parents=True, exist_ok=True)
    prior_state = read_json(STATE_FILE, {})
    prior_weights = read_json(WEIGHTS_FILE, {})
    prior_ledger = read_json(LEDGER_FILE, {"entries": []})

    default_weights = [0.6, -0.4, 0.9, -0.1]
    weights = prior_weights.get("hidden_layers_weights", default_weights)
    if (
        not isinstance(weights, list)
        or len(weights) != 4
        or not all(isinstance(value, (int, float)) for value in weights)
    ):
        raise RuntimeError("Invalid hidden_layers_weights: expected 4 numbers.")
    weights = [float(value) for value in weights]

    observations, network_events = collect_inputs()
    features = feature_vector(observations)
    result = compute_pipeline(features, weights)

    epoch = int(prior_state.get("generation_epoch", 0)) + 1
    now = datetime.now(timezone.utc).isoformat()
    evidence = {
        "schema": "cortex.evidence.v1",
        "epoch": epoch,
        "timestamp_utc": now,
        "observations": [
            {key: value for key, value in item.items() if key != "payload"}
            for item in observations
        ],
        "network_events": network_events,
        "computation": result,
        "weights_before": weights,
    }
    evidence_bytes = json.dumps(
        evidence, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    evidence_hash = hashlib.sha256(evidence_bytes).hexdigest()

    # No automatic weight update: there is no loss function or validated
    # training signal in this runtime. Persist the same weights transparently.
    state = {
        "schema": "cortex.state.v1",
        "generation_epoch": epoch,
        "status": "completed_with_observed_inputs",
        "timestamp_utc": now,
        "input_count": len(observations),
        "network_events": network_events,
        "output_score": result["output_score"],
        "evidence_hash": evidence_hash,
        "weights_updated": False,
        "note": "No validated training objective; parameters were not auto-trained.",
    }
    ledger = prior_ledger if isinstance(prior_ledger, dict) else {"entries": []}
    entries = ledger.get("entries", [])
    if not isinstance(entries, list):
        entries = []
    entries.append({
        "epoch": epoch,
        "timestamp_utc": now,
        "evidence_hash": evidence_hash,
        "input_count": len(observations),
        "output_score": result["output_score"],
    })
    ledger = {
        "schema": "cortex.ledger.v1",
        "entries": entries[-1000:],
    }

    packet = {
        "epoch": epoch,
        "evidence_hash": evidence_hash,
        "output_score": result["output_score"],
        "timestamp_utc": now,
    }
    mqtt_status = attempt_mqtt_publish(packet)
    dispatch_status = send_github_dispatch(packet)
    state["outbound_channels"] = {
        "mqtt": mqtt_status,
        "github_dispatch": dispatch_status,
    }

    atomic_json_write(STATE_FILE, state)
    atomic_json_write(WEIGHTS_FILE, {
        "schema": "cortex.weights.v1",
        "hidden_layers_weights": weights,
        "training_status": "not_trained",
    })
    atomic_json_write(LEDGER_FILE, ledger)

    summary = [
        "# Cortex Runtime Report",
        "",
        f"- **Status:** `{state['status']}`",
        f"- **Epoch:** `{epoch}`",
        f"- **UTC timestamp:** `{now}`",
        f"- **Observed inputs:** `{len(observations)}`",
        f"- **Output score:** `{result['output_score']}`",
        f"- **Evidence SHA-256:** `{evidence_hash}`",
        f"- **Weight training:** `not performed`",
        "",
        "## Input observations",
        "",
        "| Source | Type | Bytes | SHA-256 |",
        "|---|---|---:|---|",
    ]
    for item in observations:
        summary.append(
            f"| `{item['source']}` | `{item['kind']}` | {item['bytes']} "
            f"| `{item['sha256']}` |"
        )
    summary.extend([
        "",
        "## Network event results",
        "",
        "| Source | Status | Detail |",
        "|---|---|---|",
    ])
    for event in network_events:
        summary.append(
            f"| `{event['source']}` | `{event['status']}` | "
            f"{event.get('detail', '')} |"
        )
    summary.extend([
        "",
        "## Outbound channels",
        "",
        f"- MQTT: `{mqtt_status['status']}`",
        f"- GitHub Dispatch: `{dispatch_status['status']}`",
        "",
        "This report describes observed execution; it does not claim that the "
        "runtime is a trained deep-learning model or that all protocols are active.",
        "",
    ])
    report = "\n".join(summary)
    README_FILE.write_text(report, encoding="utf-8", newline="\n")

    summary_path = os.getenv("GITHUB_STEP_SUMMARY")
    if summary_path:
        with open(summary_path, "a", encoding="utf-8", newline="\n") as stream:
            stream.write(report + "\n")

    print(json.dumps({
        "status": state["status"],
        "epoch": epoch,
        "input_count": len(observations),
        "evidence_hash": evidence_hash,
        "mqtt": mqtt_status["status"],
        "github_dispatch": dispatch_status["status"],
    }, ensure_ascii=False, indent=2))

    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"CORTEX_FATAL: {type(exc).__name__}: {exc}", flush=True)
        raise
