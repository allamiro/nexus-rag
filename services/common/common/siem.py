"""NFR-2: export high-value events to the environment's existing SIEM.

REQUIREMENTS.md:93 says audit-relevant events "should be exportable to the
environment's existing SIEM"; issue #73 found the clause had no implementation
and no gap-list entry. This module is the implementation: every AuditLogEntry
row -- FR-31 already funnels each ingestion, curation, retrieval, and purge
event through that one model, from every service -- is forwarded as an
RFC 5424 syslog message with a JSON payload, the lingua franca of the SIEMs an
air-gapped DoD environment actually runs (Splunk, Elastic, ArcSight, QRadar
all ingest it natively).

Design decisions, and why:

- Hooked as a SQLAlchemy ``after_insert`` mapper event on AuditLogEntry rather
  than a wrapper every call site must remember to use. FR-31 writes happen at
  eleven call sites across four services today; a listener catches all of
  them, and any future one, with zero call-site discipline. The DB row remains
  the durable system of record -- the syslog copy is an export, not a second
  source of truth.

- ``after_insert`` fires during flush, before commit. If the surrounding
  transaction rolls back, the event has still been exported. That is the right
  failure direction for a security audit trail: a SIEM seeing an event that
  didn't durably land is noise; a SIEM missing an event that did land is a
  blind spot.

- Fail-open, never fail-closed: a SIEM outage must not take retrieval or
  ingestion down with it. Send errors are swallowed after a single WARNING
  (then demoted to DEBUG so an extended outage doesn't flood the very logs
  it's failing to forward). The durable audit row is unaffected either way.

- The payload is JSON in the syslog MSG field with ``ensure_ascii`` -- every
  control character arrives escaped, so a hostile value inside ``detail``
  cannot forge a second syslog record (the same log-injection rule
  common/log_safety.py enforces for process logs).

Configuration (all optional -- unset host means the export is disabled):

    SIEM_SYSLOG_HOST      hostname/IP of the syslog collector; unset = off
    SIEM_SYSLOG_PORT      collector port (default 514)
    SIEM_SYSLOG_PROTOCOL  "udp" (default) or "tcp" (RFC 6587 octet-counted)
"""

from __future__ import annotations

import json
import logging
import os
import socket

from sqlalchemy import event

from common.models import AuditLogEntry

logger = logging.getLogger("siem")

# RFC 5424 facility 13: "log audit" -- the facility defined for exactly this
# kind of record, not a generic localN slot a collector would have to be told
# about out-of-band.
_FACILITY_LOG_AUDIT = 13
_SEVERITY_NOTICE = 5
_SEVERITY_WARNING = 4

_NILVALUE = "-"

# Module-level so enable_siem_export() is idempotent per process: the mapper
# listener must not be registered twice (each registration would forward every
# event again), and tests need to swap the sender.
_state: dict = {"registered": False, "sender": None, "send_failed_once": False}


def _severity(action: str) -> int:
    """Denials are the events a SIEM alert actually keys on -- someone tried
    to reach something they weren't allowed to -- so they go out at WARNING;
    everything else is NOTICE (normal but significant, per RFC 5424)."""
    return _SEVERITY_WARNING if "denied" in action else _SEVERITY_NOTICE


def _msgid(action: str) -> str:
    """MSGID per RFC 5424: printable US-ASCII, no spaces, max 32 chars.
    Actions are dotted identifiers ("document.submit", "query.denied") which
    already qualify; this guards the constraint rather than trusting it."""
    cleaned = "".join(c for c in action if 33 <= ord(c) <= 126)
    return cleaned[:32] or _NILVALUE


def format_rfc5424(entry: AuditLogEntry, service: str, hostname: str, procid: int) -> bytes:
    """Render one audit row as an RFC 5424 syslog message.

    Header carries the routing/triage fields (severity, timestamp, origin
    service, action as MSGID); the MSG field carries the full event as JSON so
    the SIEM ingests the same payload the audit_log row holds -- actor
    identity, action, target, and the structured detail dict (which, per
    #125/#128, already excludes raw query text).
    """
    pri = _FACILITY_LOG_AUDIT * 8 + _severity(entry.action)
    # AuditLogEntry.created_at is a naive UTC datetime (models._utcnow); RFC
    # 5424 wants an explicit offset, so stamp the Z on.
    timestamp = entry.created_at.isoformat() + "Z"
    payload = json.dumps(
        {
            "id": str(entry.id),
            "service": service,
            "actor_sub": entry.actor_sub,
            "actor_username": entry.actor_username,
            "action": entry.action,
            "target_id": entry.target_id,
            "detail": entry.detail,
            "created_at": timestamp,
        },
        ensure_ascii=True,  # control chars arrive escaped: no forged records
        default=str,
    )
    header = (
        f"<{pri}>1 {timestamp} {hostname} nexus-rag-{service} {procid} "
        f"{_msgid(entry.action)} {_NILVALUE} "
    )
    return header.encode("ascii") + payload.encode("ascii")


class _UdpSender:
    def __init__(self, host: str, port: int) -> None:
        self._addr = (host, port)
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    def send(self, message: bytes) -> None:
        self._sock.sendto(message, self._addr)

    def close(self) -> None:
        self._sock.close()


class _TcpSender:
    """RFC 6587 octet-counted framing over a lazily-(re)connected socket.
    One reconnect attempt per send; anything beyond that is the caller's
    fail-open handling."""

    def __init__(self, host: str, port: int) -> None:
        self._addr = (host, port)
        self._sock: socket.socket | None = None

    def _connect(self) -> socket.socket:
        sock = socket.create_connection(self._addr, timeout=5)
        self._sock = sock
        return sock

    def send(self, message: bytes) -> None:
        framed = str(len(message)).encode("ascii") + b" " + message
        sock = self._sock or self._connect()
        try:
            sock.sendall(framed)
        except OSError:
            # Collector restarted and the kept-alive socket is dead: reconnect
            # once and retry, else let the error propagate to the fail-open
            # wrapper.
            self._sock = None
            self._connect().sendall(framed)

    def close(self) -> None:
        if self._sock is not None:
            self._sock.close()
            self._sock = None


def _build_sender() -> _UdpSender | _TcpSender | None:
    host = os.environ.get("SIEM_SYSLOG_HOST", "").strip()
    if not host:
        return None
    port = int(os.environ.get("SIEM_SYSLOG_PORT", "514"))
    protocol = os.environ.get("SIEM_SYSLOG_PROTOCOL", "udp").strip().lower()
    if protocol == "tcp":
        return _TcpSender(host, port)
    if protocol != "udp":
        logger.warning(
            "SIEM_SYSLOG_PROTOCOL=%r is not udp or tcp; defaulting to udp", protocol
        )
    return _UdpSender(host, port)


def _forward(mapper, connection, target: AuditLogEntry) -> None:
    """SQLAlchemy after_insert hook; mapper/connection are part of the event
    signature and deliberately unused."""
    sender = _state["sender"]
    if sender is None:
        return
    try:
        sender.send(
            format_rfc5424(target, _state["service"], _state["hostname"], _state["procid"])
        )
    except Exception:
        # Fail-open (see module docstring): the audit row is already written;
        # a SIEM outage must not break the request. Warn once, then stay quiet
        # at DEBUG so the outage doesn't flood the process logs.
        if not _state["send_failed_once"]:
            _state["send_failed_once"] = True
            logger.warning("SIEM syslog export failed; audit rows are unaffected", exc_info=True)
        else:
            logger.debug("SIEM syslog export still failing", exc_info=True)


def enable_siem_export(service: str) -> bool:
    """Enable NFR-2 SIEM export for this process; call once at service startup.

    Returns True if export is active, False if disabled (no SIEM_SYSLOG_HOST).
    Idempotent: repeated calls refresh the configuration without registering
    the listener twice.
    """
    previous = _state["sender"]
    if previous is not None:
        # Reconfiguration replaces the sender; close the old socket rather
        # than leaking it to the garbage collector.
        previous.close()
    sender = _build_sender()
    _state["sender"] = sender
    _state["service"] = service
    _state["hostname"] = socket.gethostname()
    _state["procid"] = os.getpid()
    _state["send_failed_once"] = False
    if sender is None:
        logger.info("SIEM export disabled (SIEM_SYSLOG_HOST is not set)")
        return False
    if not _state["registered"]:
        event.listen(AuditLogEntry, "after_insert", _forward)
        _state["registered"] = True
    logger.info(
        "SIEM export enabled: forwarding audit events as RFC 5424 syslog (NFR-2)"
    )
    return True
