from __future__ import annotations

from pydantic import BaseModel, computed_field

class SectionDetails(BaseModel):
    crn: str
    term: str
    subject: str
    course_number: str
    sequence_number: str
    course_title: str
    olr: bool = False

class ClassAvailability(BaseModel):
    crn: str
    term: str
    course_title: str
    subject: str
    course_number: str
    seats_available: int
    max_enrollment: int
    enrollment: int
    wait_capacity: int
    wait_count: int
    wait_available: int
    open_section: bool

    @computed_field
    @property
    def has_open_seat(self) -> bool:
        return self.seats_available > 0

    @computed_field
    @property
    def has_waitlist_spot(self) -> bool:
        return self.seats_available == 0 and self.wait_available > 0

    @computed_field
    @property
    def is_full(self) -> bool:
        return not self.has_open_seat and not self.has_waitlist_spot
