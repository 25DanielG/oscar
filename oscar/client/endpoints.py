"""Constant URLS from a HAR test on April 17, 2026"""

OSCAR_BASE = "https://students.oscar.gatech.edu"
BANNER_BASE = "https://registration.banner.gatech.edu/StudentRegistrationSsb/ssb"

# auth/session
OSCAR_HOME = f"{OSCAR_BASE}/BannerExtensibility/customPage/page/GATECH_HOMEPAGE"
OSCAR_SESSION_CHECK = f"{OSCAR_BASE}/BannerExtensibility/customPage/userSessionValidationCheck"

# class search
CLASS_SEARCH = f"{BANNER_BASE}/searchResults/searchResults"
CLASS_SEARCH_RESET = f"{BANNER_BASE}/classSearch/resetDataForm"
GET_SUBJECT = f"{BANNER_BASE}/classSearch/get_subject"
FETCH_LINKED_SECTIONS = f"{BANNER_BASE}/searchResults/fetchLinkedSections"

# registration, session setup
REGISTRATION_PAGE = f"{BANNER_BASE}/registration/registration"
TERM_SEARCH = f"{BANNER_BASE}/term/search"
CLASS_REGISTRATION_PAGE = f"{BANNER_BASE}/classRegistration/classRegistration"

# Registration, fetch model first, then submit
ADD_CRN_ITEMS = f"{BANNER_BASE}/classRegistration/addCRNRegistrationItems"
SUBMIT_REGISTRATION = f"{BANNER_BASE}/classRegistration/submitRegistration/batch"
REGISTRATION_EVENTS = f"{BANNER_BASE}/classRegistration/getRegistrationEvents"
GET_SECTION_DETAILS = f"{BANNER_BASE}/classRegistration/getSectionDetailsFromCRN"