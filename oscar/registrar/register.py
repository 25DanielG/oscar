"""Registration logic: fetch model, submit, parse response
fetch_registration_model → GET addCRNRegistrationItems (full model)
submit_registration → POST submitRegistration/batch (model + status)

success: true  AND  errorFlag: "O"  AND  statusIndicator: "R"  (in update[])
Banner always returns HTTP 200, parse the body.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import structlog

from oscar.client.models import ClassAvailability
from oscar.client.session import BannerClient, BannerError
from oscar.monitor.state import RegistrationAction

log = structlog.get_logger()

_STATUS_OK = "R" # statusIndicator — "Registered"
_ERROR_FLAG_OK = "O" # errorFlag — no errors

@dataclass
class RegistrationResult:
    success: bool
    crn: str
    action: RegistrationAction
    dry_run: bool = False
    status_indicator: str | None = None
    error_flag: str | None = None
    errors: list[str] = field(default_factory=list)
    raw_response: dict | None = None

    @property
    def failure_summary(self) -> str:
        if self.errors:
            return "; ".join(self.errors)
        return f"errorFlag={self.error_flag!r} statusIndicator={self.status_indicator!r}"

    @property
    def is_restriction_error(self) -> bool:
        combined = " ".join(self.errors).lower()
        return "major" in combined or "restriction" in combined or "restricted" in combined

async def attempt_registration(client: BannerClient, avail: ClassAvailability, action: RegistrationAction, dry_run: bool = False) -> RegistrationResult:
    """Attempt to register/waitlist a CRN. dry_run=True logs the full POST payload and returns success
    without actually submitting.
    """
    crn = avail.crn
    term = avail.term

    log.info(
        "registration_attempt",
        crn=crn,
        term=term,
        action=action.value,
        dry_run=dry_run,
    )

    try:
        model = await client.fetch_registration_model(crn, term)
    except BannerError as exc:
        log.error("fetch_model_failed", crn=crn, error=str(exc))
        return RegistrationResult(
            success=False,
            crn=crn,
            action=action,
            errors=[f"fetch_model: {exc}"],
        )

    model["courseRegistrationStatus"] = action.value

    if dry_run:
        log.info(
            "dry_run_payload",
            crn=crn,
            action=action.value,
            model=model,
        )
        return RegistrationResult(
            success=True,
            crn=crn,
            action=action,
            dry_run=True,
            status_indicator=_STATUS_OK,
            error_flag=_ERROR_FLAG_OK,
        )

    try:
        response = await client.submit_registration(model, term)
    except BannerError as exc:
        log.error("submit_failed", crn=crn, error=str(exc))
        return RegistrationResult(
            success=False,
            crn=crn,
            action=action,
            errors=[f"submit: {exc}"],
        )

    return _parse_response(response, crn, action)

def _parse_response(response: dict, crn: str, action: RegistrationAction) -> RegistrationResult:
    top_success: bool = response.get("success", False)

    # top-level errors
    top_errors: list[str] = list(response.get("errors", {}).get("errors", []))
    for crn_err in response.get("crnErrors", []):
        top_errors.extend(crn_err.get("errors", []))

    # find our CRN in the update array
    update_rows: list[dict] = (response.get("data") or {}).get("update", [])
    our_row = next(
        (r for r in update_rows if r.get("courseReferenceNumber") == crn),
        None,
    )

    status_indicator = our_row.get("statusIndicator") if our_row else None
    error_flag = our_row.get("errorFlag") if our_row else None

    # success: top_success + statusIndicator="R" + errorFlag="0" or null (no error).
    # banner can omits errorFlag on clean registration, returns null instead of "0".
    success = (
        top_success
        and status_indicator == _STATUS_OK
        and (error_flag == _ERROR_FLAG_OK or error_flag is None)
    )

    if success:
        log.info(
            "registration_success",
            crn=crn,
            action=action.value,
            status_indicator=status_indicator,
        )
    else:
        log.error(
            "registration_failed",
            crn=crn,
            action=action.value,
            top_success=top_success,
            error_flag=error_flag,
            status_indicator=status_indicator,
            errors=top_errors,
        )

    return RegistrationResult(
        success=success,
        crn=crn,
        action=action,
        status_indicator=status_indicator,
        error_flag=error_flag,
        errors=top_errors,
        raw_response=response,
    )
