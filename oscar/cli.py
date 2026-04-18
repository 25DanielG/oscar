from __future__ import annotations
import asyncio
from datetime import datetime, timezone
from pathlib import Path
import typer
from oscar.client.session import SessionExpiredError


def _relative_time(iso_utc: str) -> str:
    then = datetime.fromisoformat(iso_utc).replace(tzinfo=timezone.utc)
    s = int((datetime.now(timezone.utc) - then).total_seconds())
    if s < 60:
        return f"{s}s ago"
    if s < 3600:
        return f"{s // 60}m ago"
    return f"{s // 3600}h ago"

_EXAMPLES = """
[bold]Examples:[/bold]\n
    oscar monitor\n
    oscar check-crn 12345 --term 202608\n
    oscar auth refresh --headed\n
"""

app = typer.Typer(
    help="Georgia Tech OSCAR Registration Bot",
    no_args_is_help=True,
    rich_markup_mode="rich",
    epilog=_EXAMPLES,
)
auth_app = typer.Typer(help="Session and auth management.")
app.add_typer(auth_app, name="auth")

@app.callback()
def _setup() -> None:
    from oscar import log as applog
    from oscar.config import Settings

    settings = Settings()
    try:
        config = settings.load_config()
        applog.configure(log_dir=config.log_dir)
    except FileNotFoundError:
        applog.configure()

@auth_app.command("refresh")
def auth_refresh(
    headed: bool = typer.Option(False, "--headed", help="Force headed browser for manual login."),
) -> None:
    """Refresh OSCAR session. Headless by default; --headed for manual re-auth."""
    if headed:
        from oscar.auth.manual_login import main
        main()
    else:
        from oscar.auth.refresh_auth import main
        code = main()
        raise typer.Exit(code)

@auth_app.command("status")
def auth_status() -> None:
    """Show session cookie names and expiry times."""
    from oscar.auth.cookie_store import cookie_expiry_summary, load_cookies
    from oscar.config import Settings

    settings = Settings()
    try:
        config = settings.load_config()
        cookies = load_cookies(config.cookies_path)
    except FileNotFoundError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1)

    summary = cookie_expiry_summary(cookies)
    if not summary:
        typer.echo("No tracked cookies found in session.json.")
        return

    for e in summary:
        tag = "EXPIRED" if e["expired"] else "OK"
        typer.echo(f"{e['name']:20} {e['domain']:42} {e['expires']} [{tag}]")

@app.command("status")
def status() -> None:
    """Show session health, cookie expiry, and per-CRN seat state."""
    from oscar.auth.cookie_store import castgc_hours_remaining, load_cookies
    from oscar.auth.health_check import check_session
    from oscar.config import Settings
    from oscar.db import get_connection

    settings = Settings()
    try:
        config = settings.load_config()
    except FileNotFoundError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1)

    healthy = asyncio.run(check_session(config.cookies_path))
    session_label = "VALID" if healthy else "EXPIRED"
    expiry_str = ""
    try:
        cookies = load_cookies(config.cookies_path)
        hours = castgc_hours_remaining(cookies)
        if hours is not None:
            expiry_str = f" (expires in {hours:.0f}h)" if hours > 0 else " (EXPIRED)"
    except FileNotFoundError:
        pass

    typer.echo(f"Session: {session_label}{expiry_str}")
    typer.echo(f"Term:    {config.term}")
    typer.echo(f"CRNs:    {len(config.crns)}")
    typer.echo("")

    db_rows: dict[str, object] = {}
    if config.db_path.exists():
        try:
            conn = get_connection(config.db_path)
            rows = conn.execute(
                "SELECT crn, seats_available, wait_available, last_seen "
                "FROM crn_state WHERE term = ?",
                (config.term,),
            ).fetchall()
            conn.close()
            db_rows = {row["crn"]: row for row in rows}
        except Exception:
            pass

    for crn_cfg in config.crns:
        crn = crn_cfg.crn
        tag = f" ({crn_cfg.label})" if crn_cfg.label else ""
        prefix = f"  {crn}{tag}"

        if crn not in db_rows:
            typer.echo(f"{prefix:<30}  not yet polled")
            continue

        row = db_rows[crn]
        seats = row["seats_available"]
        wait = row["wait_available"]

        if seats > 0:
            badge = "OPEN"
            detail = f"{seats} seat{'s' if seats != 1 else ''}"
        elif wait > 0:
            badge = "WAITLIST"
            detail = f"{wait} spot{'s' if wait != 1 else ''}"
        else:
            badge = "FULL"
            detail = ""

        age = _relative_time(row["last_seen"])
        typer.echo(f"{prefix:<30}  {badge:<9}  {detail:<14}  {age}")

@app.command("check-crn")
def check_crn(crn: str, term: str = typer.Option("", "--term", "-t", help="Term code defaulting to argument in config.")) -> None:
    """Fetch live seats for a CRN."""
    from oscar.client.session import BannerClient, BannerError, SessionExpiredError
    from oscar.config import Settings

    settings = Settings()
    try:
        config = settings.load_config()
    except FileNotFoundError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1)

    _term = term or config.term

    async def _run() -> None:
        try:
            async with BannerClient.from_path(config.cookies_path, _term) as client:
                avail = await client.get_availability(crn, _term)
        except SessionExpiredError:
            typer.echo("Session expired, re-authenticate", err=True)
            raise typer.Exit(1)
        except BannerError as exc:
            typer.echo(f"Banner error: {exc}", err=True)
            raise typer.Exit(1)

        typer.echo(f"CRN:          {avail.crn}")
        typer.echo(f"Course:       {avail.subject} {avail.course_number} — {avail.course_title}")
        typer.echo(f"Term:         {avail.term}")
        typer.echo(f"Enrollment:   {avail.enrollment} / {avail.max_enrollment}")
        typer.echo(f"Seats open:   {avail.seats_available}")
        typer.echo(f"Waitlist:     {avail.wait_count} / {avail.wait_capacity}  ({avail.wait_available} open)")
        status = "OPEN" if avail.has_open_seat else ("WAITLIST" if avail.has_waitlist_spot else "FULL")
        typer.echo(f"Status:       {status}")

    asyncio.run(_run())

@app.command("monitor")
def monitor() -> None:
    """Start the polling monitor loop."""
    import signal
    from oscar.config import Settings
    from oscar.monitor.poller import Monitor
    from oscar.notify import make_notifier

    settings = Settings()
    try:
        config = settings.load_config()
    except FileNotFoundError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1)

    notifier = make_notifier(settings)
    mon = Monitor(config=config, notifier=notifier)

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    def _shutdown(sig: signal.Signals) -> None:
        import structlog
        structlog.get_logger().info("shutdown_signal", signal=sig.name)
        for task in asyncio.all_tasks(loop):
            task.cancel()

    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, lambda s=sig: _shutdown(s))

    try:
        loop.run_until_complete(mon.run())
    except (asyncio.CancelledError, KeyboardInterrupt):
        pass
    except SessionExpiredError:
        typer.echo("Session expired at startup. Run: oscar auth refresh --headed", err=True)
        raise typer.Exit(1)
    finally:
        loop.close()

@app.command("register-now")
def register_now(crn: str, term: str = typer.Option("", "--term", "-t", help="Term code defaulting to argument in config."),
                 action: str = typer.Option("RW", "--action", "-a", help="RW = open seat or WL = waitlist."), 
                 dry_run: bool = typer.Option(False, "--dry-run", help="Simulate without submitting.")) -> None:
    """Register for a CRN immediately without polling."""
    from oscar.client.session import BannerClient, BannerError, SessionExpiredError
    from oscar.config import Settings
    from oscar.monitor.state import RegistrationAction
    from oscar.registrar.register import attempt_registration
    from oscar.registrar.verify import verify_registered

    settings = Settings()
    try:
        config = settings.load_config()
    except FileNotFoundError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1)

    _term = term or config.term

    try:
        reg_action = RegistrationAction(action.upper())
    except ValueError:
        typer.echo(f"Invalid action {action!r}. Use RW or WL.", err=True)
        raise typer.Exit(1)

    async def _run() -> None:
        try:
            async with BannerClient.from_path(config.cookies_path, _term) as client:
                avail = await client.get_availability(crn, _term)
                typer.echo(f"CRN:     {avail.crn}")
                typer.echo(f"Course:  {avail.subject} {avail.course_number} — {avail.course_title}")
                typer.echo(f"Seats:   {avail.seats_available}  Waitlist: {avail.wait_available}")
                if dry_run:
                    typer.echo("Mode:    DRY RUN - no POST will be sent")
                result = await attempt_registration(client, avail, reg_action, dry_run=dry_run)
                if result.success:
                    if dry_run:
                        typer.echo("Result:  DRY RUN OK - payload logged, no POST sent.")
                    else:
                        verified = await verify_registered(client, crn, _term)
                        status = "Confirmed in schedule" if verified else "Submitted (unconfirmed)"
                        typer.echo(f"Result:  REGISTERED - {status}")
                else:
                    typer.echo(f"Result:  FAILED - {result.failure_summary}", err=True)
                    raise typer.Exit(1)
        except SessionExpiredError:
            typer.echo("Session expired. Run: oscar auth refresh --headed", err=True)
            raise typer.Exit(1)
        except BannerError as exc:
            typer.echo(f"Banner error: {exc}", err=True)
            raise typer.Exit(1)

    asyncio.run(_run())

@app.command("add")
def add_crn(crn: str, label: str = typer.Option("", "--label", "-l", help="Human-readable label.")) -> None:
    """Add a CRN to the watch list."""
    import yaml
    from oscar.config import Settings

    settings = Settings()
    path = settings.config_path
    try:
        with open(path) as f:
            data = yaml.safe_load(f)
    except FileNotFoundError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1)

    crns = data.setdefault("crns", [])
    if any(str(c.get("crn", c)) == crn for c in crns):
        typer.echo(f"CRN {crn} already in watch list.")
        raise typer.Exit(1)

    entry: dict = {"crn": crn}
    if label:
        entry["label"] = label
    crns.append(entry)

    with open(path, "w") as f:
        yaml.dump(data, f, default_flow_style=False, allow_unicode=True)
    typer.echo(f"Added CRN {crn}.")


@app.command("remove")
def remove_crn(crn: str) -> None:
    """Remove a CRN from the watch list."""
    import yaml
    from oscar.config import Settings

    settings = Settings()
    path = settings.config_path
    try:
        with open(path) as f:
            data = yaml.safe_load(f)
    except FileNotFoundError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1)

    crns = data.get("crns", [])
    new_crns = [c for c in crns if str(c.get("crn", c)) != crn]
    if len(new_crns) == len(crns):
        typer.echo(f"CRN {crn} not found in watch list.")
        raise typer.Exit(1)

    data["crns"] = new_crns
    with open(path, "w") as f:
        yaml.dump(data, f, default_flow_style=False, allow_unicode=True)
    typer.echo(f"Removed CRN {crn}.")

@app.command("logs")
def logs(tail: int = typer.Option(50, "--tail", "-n", help="Lines to show.")) -> None:
    """Tail structured JSON log entries."""
    from oscar.config import Settings

    settings = Settings()
    try:
        config = settings.load_config()
    except FileNotFoundError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1)

    log_file = config.log_dir / "oscar.log"
    if not log_file.exists():
        typer.echo("No log file yet. Start the monitor to generate logs.")
        return

    lines = log_file.read_text(encoding="utf-8").splitlines()
    for line in lines[-tail:]:
        typer.echo(line)

@app.command("dry-run")
def dry_run(crn: str, term: str = typer.Option("", "--term", "-t", help="Term code. Defaults to config.yaml term."),
            action: str = typer.Option("RW", "--action", "-a", help="RW = open seat or WL = waitlist.")) -> None:
    """Simulate full registration pipeline without submitting."""
    from oscar.client.models import ClassAvailability
    from oscar.client.session import BannerClient, BannerError, SessionExpiredError
    from oscar.config import Settings
    from oscar.monitor.state import RegistrationAction
    from oscar.registrar.register import attempt_registration

    settings = Settings()
    try:
        config = settings.load_config()
    except FileNotFoundError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1)

    _term = term or config.term

    try:
        reg_action = RegistrationAction(action.upper())
    except ValueError:
        typer.echo(f"Invalid action {action!r}. Use RW or WL.", err=True)
        raise typer.Exit(1)

    async def _run() -> None:
        try:
            async with BannerClient.from_path(config.cookies_path, _term) as client:
                avail = await client.get_availability(crn, _term)
                typer.echo(f"CRN {crn}: {avail.subject} {avail.course_number} — {avail.course_title}")
                typer.echo(f"Seats: {avail.seats_available}  Waitlist: {avail.wait_available}")
                typer.echo(f"Simulating {reg_action.value} registration (dry_run=True)…")
                result = await attempt_registration(client, avail, reg_action, dry_run=True)
                if result.success:
                    typer.echo("DRY RUN OK — payload logged, no POST sent.")
                else:
                    typer.echo(f"DRY RUN FAILED: {result.failure_summary}", err=True)
        except SessionExpiredError:
            typer.echo("Session expired. Run: oscar auth refresh --headed", err=True)
            raise typer.Exit(1)
        except BannerError as exc:
            typer.echo(f"Banner error: {exc}", err=True)
            raise typer.Exit(1)

    asyncio.run(_run())

if __name__ == "__main__":
    app()
