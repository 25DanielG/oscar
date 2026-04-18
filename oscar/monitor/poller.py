"""
Async monitor loop. One poll routine for each CRN running concurrently, with a shared BannerClient.
Session expiry handled with asyncio.Event and Lock to coordinate between pollers and recovery loop.
Session expiry flow:
    - poller detects expiry → acquires lock → clears event → notifies user
    - recovery loop retries _open_client() every 30s
    - success → replaces self._client → sets event → all pollers resume

"""

from __future__ import annotations

import asyncio
import random
import sqlite3
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any
import structlog

from oscar import log as applog
from oscar.client.models import ClassAvailability
from oscar.client.session import BannerClient, BannerError, SchemaDriftError, SessionExpiredError
from oscar.config import Config
from oscar.db import init_db
from oscar.monitor import state as st
from oscar.monitor.state import RegistrationAction
from oscar.notify.base import Notifier
from oscar.notify.pushover import PRIORITY_HIGH, PRIORITY_LOW, PRIORITY_NORMAL

log = structlog.get_logger()

OnTrigger = Callable[[ClassAvailability, RegistrationAction], Awaitable[None]]

_RECOVERY_INTERVAL = 30.0 # seconds between session-restore attempts
_HEARTBEAT_INTERVAL = 86_400.0 # 24 hours
_EXPIRY_WARN_HOURS = 36.0

class Monitor:
    def __init__(self, config: Config, notifier: Notifier | None = None, on_trigger: OnTrigger | None = None) -> None:
        self._config = config
        self._notifier = notifier
        self._on_trigger: OnTrigger = on_trigger or self._do_register
        self._client: BannerClient | None = None
        self._db: sqlite3.Connection | None = None
        self._session_ok: asyncio.Event | None = None
        self._expiry_lock: asyncio.Lock | None = None
        # serialise concurrent registration attempts, two CRNs opening simultaneously
        self._reg_lock: asyncio.Lock | None = None
        # silent retry after a major-restriction failure
        self._restriction_pending: set[str] = set()
        self._drift_alerted: set[str] = set()
        self._crn_cfg_map = {c.crn: c for c in config.crns}

    # entry point
    async def run(self) -> None:
        applog.configure(log_dir=self._config.log_dir)
        self._session_ok = asyncio.Event()
        self._expiry_lock = asyncio.Lock()
        self._reg_lock = asyncio.Lock()

        log.info("monitor_starting", crns=[c.crn for c in self._config.crns], term=self._config.term)

        self._db = init_db(self._config.db_path)

        try:
            self._client = await self._open_client()
        except SessionExpiredError:
            log.error("startup_session_expired")
            await self._notify(
                "OSCAR: Startup Failed",
                "Session expired at startup. Run: oscar auth refresh --headed",
                priority=PRIORITY_HIGH,
            )
            raise

        self._session_ok.set()
        log.info("monitor_ready")
        await self._check_cookie_expiry()

        tasks = [
            asyncio.create_task(self._poll_crn_loop(crn_cfg), name=f"poll-{crn_cfg.crn}")
            for crn_cfg in self._config.crns
        ]
        heartbeat = asyncio.create_task(self._heartbeat_loop(), name="heartbeat")
        all_tasks = tasks + [heartbeat]

        try:
            await asyncio.gather(*all_tasks)
        except asyncio.CancelledError:
            log.info("monitor_stopping")
            raise
        finally:
            for t in all_tasks:
                t.cancel()
            if self._client:
                await self._client.__aexit__(None, None, None)
            if self._db:
                self._db.close()

    # poll loop for each CRN
    async def _poll_crn_loop(self, crn_cfg: Any) -> None:
        crn = crn_cfg.crn
        term = self._config.term
        poll = self._config.poll

        # stagger startup in case too many CRNs
        await asyncio.sleep(random.uniform(0, poll.base_interval))

        consecutive_errors = 0

        while True:
            assert self._session_ok is not None
            await self._session_ok.wait()

            try:
                await self._do_poll(crn, term)
                consecutive_errors = 0
            except asyncio.CancelledError:
                raise
            except SessionExpiredError:
                await self._handle_session_expiry()
                continue # skip sleep, re-check session_ok immediately
            except SchemaDriftError as exc:
                log.error("schema_drift_detected", crn=crn, error=str(exc))
                if crn not in self._drift_alerted:
                    self._drift_alerted.add(crn)
                    await self._notify(
                        f"OSCAR: API Schema Drift — CRN {crn}",
                        f"Banner response shape changed.\nCRN {crn} skipped until fixed.\n{exc}",
                        priority=PRIORITY_HIGH,
                    )
            except BannerError as exc:
                consecutive_errors += 1
                log.error("poll_banner_error", crn=crn, error=str(exc), consecutive=consecutive_errors)
                if consecutive_errors == 5:
                    await self._notify(
                        f"OSCAR: Poll Errors CRN {crn}",
                        f"{consecutive_errors} consecutive errors:\n{exc}",
                    )
            except Exception as exc:
                consecutive_errors += 1
                log.exception("poll_unexpected_error", crn=crn, error=str(exc))

            jitter = random.uniform(-poll.jitter, poll.jitter)
            delay = max(10.0, float(poll.base_interval) + jitter)
            await asyncio.sleep(delay)

    # single poll
    async def _do_poll(self, crn: str, term: str) -> None:
        assert self._client is not None
        assert self._db is not None

        avail = await self._client.get_availability(crn, term)

        prev = st.get_state(self._db, crn, term)
        changed = st.state_changed(prev, avail)
        action = st.detect_transition(prev, avail)

        st.log_poll(self._db, avail, changed or action is not None)
        st.upsert_state(self._db, avail)

        if action is not None:
            log.warning(
                "trigger_fired",
                crn=crn,
                action=action.value,
                seats=avail.seats_available,
                wait=avail.wait_available,
            )
            await self._on_trigger(avail, action)
        elif crn in self._restriction_pending:
            if avail.has_open_seat:
                log.info("restriction_retry", crn=crn, seats=avail.seats_available)
                await self._do_restriction_retry(avail, RegistrationAction.REGISTER)
            elif avail.has_waitlist_spot:
                log.info("restriction_retry_waitlist", crn=crn, wait=avail.wait_available)
                await self._do_restriction_retry(avail, RegistrationAction.WAITLIST)
        elif changed:
            prev_seats = prev.seats_available if prev else None
            prev_wait = prev.wait_available if prev else None
            log.info(
                "state_changed",
                crn=crn,
                seats=avail.seats_available,
                wait=avail.wait_available,
                prev_seats=prev_seats,
                prev_wait=prev_wait,
            )
            await self._notify(
                f"OSCAR: {avail.subject} {avail.course_number} ({crn}) updated",
                f"{avail.course_title}\n"
                f"Seats: {prev_seats} → {avail.seats_available}\n"
                f"Waitlist: {prev_wait} → {avail.wait_available}",
                priority=PRIORITY_LOW,
            )
        else:
            log.debug("poll_no_change", crn=crn, seats=avail.seats_available, wait=avail.wait_available)

    # session expiry handling
    async def _handle_session_expiry(self) -> None:
        assert self._session_ok is not None
        assert self._expiry_lock is not None

        async with self._expiry_lock:
            if not self._session_ok.is_set():
                # another poller is already handling recovery, wait for it to finish
                return
            # first poller to detect expiry, fix
            self._session_ok.clear()

        log.error("session_expired_recovery_started")
        await self._notify(
            "OSCAR: Session Expired",
            "Run on laptop: oscar auth refresh --headed\nThen: scripts/refresh_auth.sh",
            priority=PRIORITY_HIGH,
        )

        while True:
            await asyncio.sleep(_RECOVERY_INTERVAL)
            log.info("session_recovery_attempt")
            try:
                new_client = await self._open_client()
            except SessionExpiredError:
                log.info("session_still_expired_retrying")
                continue
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.error("session_recovery_error", error=str(exc))
                continue

            old_client = self._client
            self._client = new_client

            if old_client is not None:
                try:
                    await old_client.__aexit__(None, None, None)
                except Exception:
                    pass

            assert self._session_ok is not None
            self._session_ok.set()
            log.info("session_restored")
            await self._notify("OSCAR: Session Restored", "Monitoring resumed.")
            return

    # heartbeat
    async def _heartbeat_loop(self) -> None:
        while True:
            await asyncio.sleep(_HEARTBEAT_INTERVAL)
            crn_count = len(self._config.crns)
            log.info("heartbeat", crns=crn_count, term=self._config.term)
            await self._notify(
                "OSCAR Bot: Heartbeat",
                f"Alive. Monitoring {crn_count} CRN(s) for {self._config.term}.",
                priority=PRIORITY_LOW,
            )
            await self._check_cookie_expiry()

    # helpers

    async def _check_cookie_expiry(self) -> None:
        from oscar.auth.cookie_store import castgc_hours_remaining, load_cookies
        try:
            cookies = load_cookies(self._config.cookies_path)
        except FileNotFoundError:
            return
        hours = castgc_hours_remaining(cookies)
        if hours is None or hours < 0:
            return
        if hours < _EXPIRY_WARN_HOURS:
            log.warning("cookie_expiry_warning", hours_remaining=round(hours, 1))
            await self._notify(
                "OSCAR: Session Expiring Soon",
                f"CASTGC expires in {hours:.0f}h.\nRun on laptop: oscar auth refresh --headed\nThen: scripts/refresh_auth.sh",
                priority=PRIORITY_HIGH,
            )

    async def _open_client(self) -> BannerClient:
        client = BannerClient.from_path(self._config.cookies_path, self._config.term)
        await client.__aenter__()
        return client

    async def _notify(self, title: str, message: str, priority: int = PRIORITY_NORMAL) -> None:
        if self._notifier is None:
            return
        try:
            await self._notifier.send(title, message, priority)
        except Exception as exc:
            log.error("notification_error", title=title, error=str(exc))

    async def _do_restriction_retry(self, avail: ClassAvailability, action: RegistrationAction) -> None:
        """Silent registration retry for major-restricted CRNs. No 'SEAT OPEN' noise, only notifies on success or non-restriction failure."""
        from oscar.registrar.register import attempt_registration
        from oscar.registrar.verify import verify_registered

        assert self._client is not None
        assert self._reg_lock is not None

        async with self._reg_lock:
            result = await attempt_registration(
                self._client,
                avail,
                action,
                dry_run=self._config.dry_run,
            )

        if result.success:
            self._restriction_pending.discard(avail.crn)
            verified = False
            if not result.dry_run:
                verified = await verify_registered(self._client, avail.crn, avail.term)
            status_line = (
                "DRY RUN — not submitted"
                if result.dry_run
                else ("Confirmed in schedule ✓" if verified else "Submitted (unconfirmed)")
            )
            await self._notify(
                f"OSCAR: REGISTERED — {avail.crn}",
                f"{avail.subject} {avail.course_number} {avail.course_title}\n"
                f"Action: {action.value}\n"
                f"{status_line}",
                priority=PRIORITY_HIGH,
            )
        elif not result.is_restriction_error:
            self._restriction_pending.discard(avail.crn)
            log.error("restriction_retry_non_restriction_failure", crn=avail.crn, reason=result.failure_summary)
            await self._notify(
                f"OSCAR: Retry Failed — {avail.crn}",
                f"{avail.subject} {avail.course_number} {avail.course_title}\n"
                f"Non-restriction error — stopped retrying.\nReason: {result.failure_summary}",
                priority=PRIORITY_HIGH,
            )
        # else: still restricted → stay silent, keep in pending

    async def _do_register(self, avail: ClassAvailability, action: RegistrationAction) -> None:
        """Attempt registration when a seat/waitlist opens. Serialised with _reg_lock."""
        from oscar.registrar.register import attempt_registration
        from oscar.registrar.verify import verify_registered

        assert self._client is not None
        assert self._reg_lock is not None

        action_label = "SEAT OPEN" if action == RegistrationAction.REGISTER else "WAITLIST OPEN"
        await self._notify(
            f"OSCAR: {action_label} — {avail.crn}",
            f"{avail.subject} {avail.course_number} {avail.course_title}\n"
            f"Seats: {avail.seats_available}  Waitlist: {avail.wait_available}\n"
            f"Attempting registration…",
            priority=PRIORITY_HIGH,
        )

        async with self._reg_lock:
            result = await attempt_registration(
                self._client,
                avail,
                action,
                dry_run=self._config.dry_run,
            )

        if result.success:
            self._restriction_pending.discard(avail.crn)
            verified = False
            if not result.dry_run:
                verified = await verify_registered(
                    self._client, avail.crn, avail.term
                )

            status_line = (
                "DRY RUN — not submitted"
                if result.dry_run
                else ("Confirmed in schedule ✓" if verified else "Submitted (unconfirmed)")
            )
            await self._notify(
                f"OSCAR: REGISTERED — {avail.crn}",
                f"{avail.subject} {avail.course_number} {avail.course_title}\n"
                f"Action: {action.value}\n"
                f"{status_line}",
                priority=PRIORITY_HIGH,
            )
        else:
            if result.is_restriction_error:
                crn_cfg = self._crn_cfg_map.get(avail.crn)
                if crn_cfg and crn_cfg.retry_on_restriction and avail.crn not in self._restriction_pending:
                    self._restriction_pending.add(avail.crn)
                    log.info("restriction_retry_armed", crn=avail.crn)
                    await self._notify(
                        f"OSCAR: Major Restriction — {avail.crn}",
                        f"{avail.subject} {avail.course_number} {avail.course_title}\n"
                        f"Major restriction active. Retrying silently every poll until lifted.",
                        priority=PRIORITY_NORMAL,
                    )
            else:
                self._restriction_pending.discard(avail.crn)
                await self._notify(
                    f"OSCAR: Registration FAILED — {avail.crn}",
                    f"{avail.subject} {avail.course_number} {avail.course_title}\n"
                    f"Reason: {result.failure_summary}",
                    priority=PRIORITY_HIGH,
                )
