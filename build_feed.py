from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from html import unescape
from html.parser import HTMLParser
from pathlib import Path
from typing import Iterable
from urllib.parse import urljoin
from urllib.request import Request, urlopen


PROJECT_ROOT = Path(__file__).resolve().parent
FEED_PATH = PROJECT_ROOT / "feed.json"
FEEDS_DIR = PROJECT_ROOT / "feeds"
BOSTON_FEED_PATH = FEEDS_DIR / "boston.json"
TODAY = datetime.now()
CURRENT_YEAR = TODAY.year
LOOKAHEAD_DAYS = 365
PASSED_ARCHIVE_DAYS = 60


PARKING_URL = "https://www.cambridgema.gov/en/iwantto/applyforaparkingpermit"
EXEMPTIONS_URL = "https://www.cambridgema.gov/Services/taxpayerexemptions"
ELECTION_NEWS_URL = "https://www.cambridgema.gov/Departments/electioncommission/news"
CENSUS_FORM_URL = "https://www.cambridgema.gov/Departments/electioncommission/census/form"
LIBRARY_URL = "https://www.cambridgema.gov/en/Departments/cambridgepubliclibrary/"
LIBRARY_CALENDAR_URL = "https://www.cambridgema.gov/Departments/cambridgepubliclibrary/calendar?department=cpl&view=Month&page=1&resultsperpage=15"
CRLS_CALENDAR_URL = "https://crls.cpsd.us/calendar-link"
SCHOOL_COMMITTEE_URL = "https://www.cpsd.us/all-events"
PRIMEGOV_URL = "https://cambridgema.primegov.com/public/portal"
HOUSING_APPLICANTS_URL = "https://www.cambridgema.gov/CDD/housing/forapplicants"
RENTAL_POOL_URL = "https://www.cambridgema.gov/CDD/housing/forapplicants/rentalapplicantpool"
RESALE_POOL_URL = "https://www.cambridgema.gov/CDD/housing/forapplicants/resalepool"
MIDDLE_INCOME_URL = "https://www.cambridgema.gov/CDD/housing/forapplicants/middleincomerentalprogram"
HOUSING_TRUST_URL = "https://www.cambridgema.gov/CDD/housing/housingtrust"
CAMBRIDGE_ARTS_URL = "https://www.cambridgema.gov/arts"
CAMBRIDGE_ARTS_CALENDAR_URL = "https://www.cambridgema.gov/arts/Calendar"
CPHD_MINI_GRANTS_URL = "https://www.cambridgepublichealth.org/services/mini-grants/"
CPP_URL = "https://earlychildhoodcambridge.org/cpp/"

BOSTON_DOG_LICENSE_URL = "https://www.boston.gov/departments/animal-care-and-control/how-license-your-dog"
BOSTON_TAX_EXEMPTIONS_URL = "https://www.boston.gov/departments/assessing/filing-property-tax-exemption"
BOSTON_ELECTIONS_URL = "https://www.boston.gov/departments/elections/boston-elections-commission"
BOSTON_HOUSING_URL = "https://www.boston.gov/departments/housing"
BOSTON_AGE_STRONG_GRANTS_URL = "https://www.boston.gov/departments/age-strong-commission/age-strong-2025-grantees"
BOSTON_BPS_ENROLL_URL = "https://www.bostonpublicschools.org/enroll"
BOSTON_BPL_ESOL_URL = "https://www.bpl.org/esol/"

SOURCE_PARSERS = [
    ("parking permits", "deadlines", "Cambridge parking permit renewal deadline", "stable city page", "strong deadline signal"),
    ("tax exemptions", "deadlines", "annual Cambridge personal tax exemptions filing deadline", "stable city page", "strong deadline signal"),
    ("annual census", "free services", "annual city census return notice", "election news index", "important evergreen civic action"),
    ("public health", "health", "Cambridge Public Health mini-grants", "public health service page", "deadline-driven health opportunity"),
    ("cpp", "students and families", "Cambridge Preschool Program application timeline", "office of early childhood page", "family application opportunity"),
    ("housing opportunities", "free services", "Cambridge housing application pages", "city housing pages", "rolling availability notices"),
    ("housing trust meetings", "coming up", "Cambridge Affordable Housing Trust meeting page", "city housing page", "date-based meeting extraction"),
    ("school committee", "coming up", "CPSD all events page", "school calendar page", "keyword-filtered civic and family events"),
    ("crls calendar", "coming up", "CRLS items inferred from CPSD calendar markup", "derived from school calendar page", "student-focused subset"),
    ("primegov portal", "ongoing", "PrimeGov public meetings portal", "public meetings portal", "evergreen civic lookup"),
    ("library programs", "free services", "Cambridge Public Library calendar", "library calendar pages", "keyword-filtered useful programs"),
    ("cambridge arts", "arts and culture", "Cambridge Arts events portal", "arts homepage", "lightweight arts discovery source"),
]

BOSTON_SOURCE_PARSERS = [
    ("boston dog license", "deadlines", "annual Boston dog license deadline", "city service page", "strong annual renewal signal"),
    ("boston tax exemptions", "deadlines", "Boston property tax exemption deadline", "city assessing page", "strong annual filing signal"),
    ("boston elections", "voting and civics", "Boston annual listing and elections page", "elections department page", "important civic participation source"),
    ("boston housing", "housing", "Boston housing and Metrolist page", "office of housing page", "evergreen housing opportunity source"),
    ("boston age strong", "older adults", "Age Strong rolling grants", "city grants page", "deadline-driven older adult opportunity"),
    ("boston bps enrollment", "students and families", "Boston Public Schools enrollment page", "district enrollment page", "family-facing application source"),
    ("boston bpl esol", "education and work", "Boston Public Library ESOL page", "library adult learning page", "evergreen free service source"),
]


@dataclass
class FeedItem:
    title: str
    date: str | None
    display_date: str
    time: str | None
    location: str
    description: str
    action_label: str
    url: str
    cost: str
    pathways: list[str]
    source: str

    def to_dict(self) -> dict:
        return {
            "title": self.title,
            "date": self.date,
            "display_date": self.display_date,
            "time": self.time,
            "location": self.location,
            "description": self.description,
            "action_label": self.action_label,
            "url": self.url,
            "cost": self.cost,
            "pathways": self.pathways,
            "source": self.source,
        }


class SimpleHTML(HTMLParser):
    BLOCK_TAGS = {
        "p",
        "div",
        "section",
        "article",
        "header",
        "footer",
        "main",
        "nav",
        "ul",
        "ol",
        "li",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "br",
    }

    def __init__(self) -> None:
        super().__init__()
        self.text_parts: list[str] = []
        self.links: list[dict[str, str]] = []
        self._current_href: str | None = None
        self._current_link_text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in self.BLOCK_TAGS:
            self.text_parts.append("\n")

        if tag == "a":
            attrs_dict = dict(attrs)
            self._current_href = attrs_dict.get("href")
            self._current_link_text = []

    def handle_endtag(self, tag: str) -> None:
        if tag in self.BLOCK_TAGS:
            self.text_parts.append("\n")

        if tag == "a":
            text = clean_whitespace(" ".join(self._current_link_text))
            if self._current_href and text:
                self.links.append({"text": text, "href": self._current_href})
            self._current_href = None
            self._current_link_text = []

    def handle_data(self, data: str) -> None:
        if not data.strip():
            return

        self.text_parts.append(data)
        if self._current_href is not None:
            self._current_link_text.append(data)

    def text_lines(self) -> list[str]:
        text = unescape("".join(self.text_parts))
        lines = [clean_whitespace(line) for line in text.splitlines()]
        return [line for line in lines if line]


def clean_whitespace(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def contains_keyword(haystack: str, keyword: str) -> bool:
    normalized = haystack.lower()
    keyword = keyword.lower()
    if re.search(r"[a-z0-9]", keyword) and " " not in keyword and "-" not in keyword:
        return re.search(rf"\b{re.escape(keyword)}\b", normalized) is not None
    return keyword in normalized


def contains_any_keyword(haystack: str, keywords: Iterable[str]) -> bool:
    return any(contains_keyword(haystack, keyword) for keyword in keywords)


def fetch_html(url: str) -> str:
    request = Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (compatible; CivicDeadlinesBot/1.0; +https://localhost)"
        },
    )
    with urlopen(request, timeout=30) as response:
        return response.read().decode("utf-8", errors="ignore")


def first_sentence(text: str) -> str:
    cleaned = clean_whitespace(unescape(text))
    if not cleaned:
        return ""

    match = re.search(r"(.+?[.!?])(?:\s|$)", cleaned)
    sentence = match.group(1) if match else cleaned
    if sentence[-1] not in ".!?":
        sentence += "."
    return sentence


def parse_month_day_year(text: str) -> datetime | None:
    cleaned = clean_whitespace(text).replace(" ,", ",")
    for fmt in ("%B %d, %Y", "%b %d, %Y"):
        try:
            return datetime.strptime(cleaned, fmt)
        except ValueError:
            continue
    return None


def parse_month_day(text: str, year: int = CURRENT_YEAR) -> datetime | None:
    cleaned = clean_whitespace(text).replace(",", "")
    for fmt in ("%B %d %Y", "%b %d %Y"):
        try:
            return datetime.strptime(f"{cleaned} {year}", fmt)
        except ValueError:
            continue
    return None


def parse_clock_time(text: str) -> str | None:
    match = re.search(r"(\d{1,2}:\d{2}\s*[AP]M)", text, re.IGNORECASE)
    if not match:
        return None
    return match.group(1).upper().replace(" ", " ")


def parse_time_range_start(text: str) -> str | None:
    match = re.search(r"(\d{1,2}:\d{2}\s*[AP]M)", clean_whitespace(text), re.IGNORECASE)
    if not match:
        return None
    return match.group(1).upper()


def iso_date(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.strftime("%Y-%m-%d")


def display_date(value: datetime | None) -> str:
    if value is None:
        return "Ongoing"
    return value.strftime("%A, %B %-d, %Y")


def should_keep_dated_item(value: datetime | None) -> bool:
    if value is None:
        return True
    earliest = TODAY.date() - timedelta(days=PASSED_ARCHIVE_DAYS)
    latest = TODAY.date() + timedelta(days=LOOKAHEAD_DAYS)
    return earliest <= value.date() <= latest


def parse_numeric_date(text: str) -> datetime | None:
    cleaned = clean_whitespace(text)
    for fmt in ("%m/%d/%Y", "%m/%d/%y"):
        try:
            return datetime.strptime(cleaned, fmt)
        except ValueError:
            continue
    return None


def title_case(value: str) -> str:
    small_words = {"and", "or", "for", "of", "the", "to", "a", "an", "in", "on", "with"}
    words = clean_whitespace(value).split(" ")
    output: list[str] = []
    for index, word in enumerate(words):
        if index > 0 and word.lower() in small_words:
            output.append(word.lower())
        else:
            output.append(word[:1].upper() + word[1:])
    return " ".join(output)


def slugify(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")


def parse_library_month_label(text: str) -> tuple[int, int] | None:
    cleaned = clean_whitespace(text)
    for fmt in ("%B %Y", "%B (%Y)"):
        try:
            parsed = datetime.strptime(cleaned, fmt)
            return parsed.year, parsed.month
        except ValueError:
            continue
    return None


def strip_tags(value: str) -> str:
    return clean_whitespace(unescape(re.sub(r"<[^>]+>", " ", value)))


def extract_library_month_options(html: str) -> list[tuple[int, int]]:
    options = re.findall(r'<option[^>]*value="(\d{8})T\d{6}"[^>]*>', html, re.IGNORECASE)
    months: list[tuple[int, int]] = []
    seen: set[tuple[int, int]] = set()

    for value in options:
        year = int(value[:4])
        month = int(value[4:6])
        key = (year, month)
        if key in seen:
            continue
        seen.add(key)
        months.append(key)

    return months


def current_and_future_months(months: list[tuple[int, int]], max_months: int = 8) -> list[tuple[int, int]]:
    current_key = (TODAY.year, TODAY.month)
    filtered = [month for month in months if month >= current_key]
    return filtered[:max_months]


def build_library_month_url(year: int, month: int) -> str:
    return (
        "https://www.cambridgema.gov/Departments/cambridgepubliclibrary/calendar"
        f"?start={year}{month:02d}01T000000&department=cpl&view=Month&page=1&resultsperpage=15"
    )


def event_detail_url(base_url: str, href: str) -> str:
    return urljoin(base_url, href)


def dedupe_items(items: Iterable[FeedItem]) -> list[FeedItem]:
    seen: set[tuple[str, str | None]] = set()
    unique: list[FeedItem] = []

    for item in items:
        key = (item.title, item.date)
        if key in seen:
            continue
        seen.add(key)
        unique.append(item)

    return unique


def sort_key(item: FeedItem) -> tuple[int, str]:
    if item.date is None:
        return (1, item.title)
    return (0, item.date)


def parse_occurrence_date(occurrence_id: str) -> datetime | None:
    parts = occurrence_id.split("_")
    if len(parts) < 2:
        return None

    timestamp = parts[1]
    for fmt in ("%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%d"):
        try:
            return datetime.strptime(timestamp, fmt)
        except ValueError:
            continue
    return None


def parse_datetime_attr(value: str) -> datetime | None:
    cleaned = value.strip()
    if cleaned.endswith("Z"):
        cleaned = cleaned[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(cleaned).replace(tzinfo=None)
    except ValueError:
        return None


def format_hour_minute(hour: str, minute: str, meridian: str) -> str:
    return f"{int(hour)}:{minute} {meridian.upper()}"


def extract_school_calendar_events(html: str) -> list[dict[str, str | datetime | None]]:
    events: list[dict[str, str | datetime | None]] = []
    anchor_pattern = re.compile(
        r'title="(?P<title>[^"]+)"\s+data-occur-id="(?P<occur>[^"]+)"[^>]*href="#"',
        re.IGNORECASE,
    )
    matches = list(anchor_pattern.finditer(html))

    for index, match in enumerate(matches):
        next_start = matches[index + 1].start() if index + 1 < len(matches) else len(html)
        segment = html[match.end() : next_start]
        title = strip_tags(match.group("title"))
        lowered = title.lower()
        if not title or "cancel" in lowered or "rescheduled:" in lowered:
            continue

        start_datetime_match = re.search(r'<time[^>]*datetime="([^"]+)"[^>]*class="fsStartTime"', segment, re.IGNORECASE)
        start_datetime = parse_datetime_attr(start_datetime_match.group(1)) if start_datetime_match else None
        event_date = start_datetime or parse_occurrence_date(match.group("occur"))
        if event_date is None:
            continue

        if 'fsAllDayEvent' in segment:
            time_text = None
        elif start_datetime_match:
            time_parts = re.search(
                r'<span class="fsHour">\s*(\d{1,2})</span>:\s*<span class="fsMinute">(\d{2})</span>\s*<span class="fsMeridian">\s*([AP]M)\s*</span>',
                segment,
                re.IGNORECASE,
            )
            time_text = format_hour_minute(*time_parts.groups()) if time_parts else event_date.strftime("%-I:%M %p")
        else:
            time_text = None

        location_match = re.search(r'<div class="fsLocation">\s*(.*?)\s*(?:</div>|<)', segment, re.IGNORECASE | re.DOTALL)
        location = strip_tags(location_match.group(1)) if location_match else "Cambridge Public Schools"

        events.append(
            {
                "title": title,
                "date": event_date,
                "time": time_text,
                "location": location or "Cambridge Public Schools",
            }
        )

    return events


def parse_parking_deadline() -> list[FeedItem]:
    html = fetch_html(PARKING_URL)
    parser = SimpleHTML()
    parser.feed(html)
    text = "\n".join(parser.text_lines())

    deadline_match = re.search(
        r"renew or apply for your Resident and/or Visitor Parking Permit by ([A-Z][a-z]+ \d{1,2}, \d{4})",
        text,
    )

    if not deadline_match:
        raise ValueError("Could not find parking permit deadline.")

    deadline = parse_month_day_year(deadline_match.group(1))
    if not should_keep_dated_item(deadline):
        return []

    return [
        FeedItem(
            title="Renew Cambridge Parking Permit",
            date=iso_date(deadline),
            display_date=display_date(deadline),
            time=None,
            location="Cambridge",
            description="Resident and visitor parking permits should be renewed online or in person before the city deadline.",
            action_label="Renew Parking Permit",
            url=PARKING_URL,
            cost="Varies by permit type",
            pathways=["renewals"],
            source="Cambridge Parking Permits",
        )
    ]


def parse_tax_exemptions() -> list[FeedItem]:
    html = fetch_html(EXEMPTIONS_URL)
    parser = SimpleHTML()
    parser.feed(html)
    text = "\n".join(parser.text_lines())

    deadline_match = re.search(
        r"Applications are due .*? by ([A-Z][a-z]+ \d{1,2}) for each Fiscal Year",
        text,
    )

    if not deadline_match:
        raise ValueError("Could not find taxpayer exemptions deadline.")

    deadline = parse_month_day_year(f"{deadline_match.group(1)}, {CURRENT_YEAR}")
    if not should_keep_dated_item(deadline):
        return []

    return [
        FeedItem(
            title="Apply for Cambridge Personal Tax Exemptions",
            date=iso_date(deadline),
            display_date=display_date(deadline),
            time=None,
            location="Assessing Office",
            description="Eligible residents can apply for Cambridge personal tax exemptions by the annual filing deadline.",
            action_label="Open Tax Exemptions",
            url=EXEMPTIONS_URL,
            cost="Free",
            pathways=["renewals", "older_adults"],
            source="Cambridge Taxpayer Exemptions",
        )
    ]


def parse_census_notice() -> list[FeedItem]:
    html = fetch_html(ELECTION_NEWS_URL)
    parser = SimpleHTML()
    parser.feed(html)
    census_links: list[tuple[int, str, str]] = []
    for link in parser.links:
        text = clean_whitespace(link["text"])
        if "annual city census" not in text.lower():
            continue
        year_match = re.search(r"\b(20\d{2})\b", text)
        year = int(year_match.group(1)) if year_match else 0
        census_links.append((year, text, event_detail_url(ELECTION_NEWS_URL, link["href"])))

    if not census_links:
        raise ValueError("Could not find annual census notice.")

    year, title, detail_url = max(census_links, key=lambda item: item[0])
    description_year = str(year) if year else "current"

    return [
        FeedItem(
            title="Return Your Cambridge Annual City Census",
            date=None,
            display_date="Return as soon as possible",
            time=None,
            location="Online, mail, or drop box",
            description=f"Cambridge residents should return the {description_year} annual city census to protect voting rights and support municipal services.",
            action_label="Open Census Form",
            url=CENSUS_FORM_URL,
            cost="Free",
            pathways=["voting_civics", "renewals", "just_browsing"],
            source=f"Cambridge Election Commission ({title})",
        )
    ]


def parse_public_health_minigrants() -> list[FeedItem]:
    html = fetch_html(CPHD_MINI_GRANTS_URL)
    parser = SimpleHTML()
    parser.feed(html)
    text = "\n".join(parser.text_lines())

    if "Health Promotion Mini-Grants" not in text:
        raise ValueError("Could not find Health Promotion Mini-Grants section.")

    deadline_match = re.search(r"application deadline is ([A-Z][a-z]+ \d{1,2}, \d{4})", text, re.IGNORECASE)
    if not deadline_match:
        deadline_match = re.search(r"deadline[:\s]+([A-Z][a-z]+ \d{1,2}, \d{4})", text, re.IGNORECASE)
    if not deadline_match:
        raise ValueError("Could not find mini-grants deadline.")

    deadline = parse_month_day_year(deadline_match.group(1))
    if not should_keep_dated_item(deadline):
        return []

    return [
        FeedItem(
            title="Apply for Cambridge Public Health Mini-Grants",
            date=iso_date(deadline),
            display_date=display_date(deadline),
            time=None,
            location="Cambridge",
            description="Community groups, schools, businesses, and other local organizations can apply for Cambridge Public Health mini-grants supporting healthy eating, physical activity, or youth wellness.",
            action_label="Open Mini-Grants",
            url=CPHD_MINI_GRANTS_URL,
            cost="Free",
            pathways=["health", "just_browsing"],
            source="Cambridge Public Health Department",
        )
    ]


def parse_cpp_application() -> list[FeedItem]:
    html = fetch_html(CPP_URL)
    parser = SimpleHTML()
    parser.feed(html)
    text = "\n".join(parser.text_lines())

    if "Cambridge Preschool Program" not in text:
        raise ValueError("Could not find Cambridge Preschool Program page.")

    deadline_match = re.search(
        r"Applications submitted by ([A-Z][a-z]+ \d{1,2}(?:st|nd|rd|th)?[,]?\s+20\d{2}) will be included",
        text,
        re.IGNORECASE,
    )
    if not deadline_match:
        deadline_match = re.search(
            r"between [A-Z][a-z]+ \d{1,2}, \d{4} and ([A-Z][a-z]+ \d{1,2}, \d{4})",
            text,
            re.IGNORECASE,
        )
    if not deadline_match:
        raise ValueError("Could not find CPP application deadline.")

    deadline_text = clean_whitespace(deadline_match.group(1))
    deadline_text = re.sub(r"(\d{1,2})(st|nd|rd|th)", r"\1", deadline_text)
    deadline_text = deadline_text.replace(" ,", ",")
    deadline = parse_month_day_year(deadline_text)
    if not should_keep_dated_item(deadline):
        return []

    return [
        FeedItem(
            title="Apply for Cambridge Preschool Program Spring Match",
            date=iso_date(deadline),
            display_date=display_date(deadline),
            time=None,
            location="Cambridge",
            description="Cambridge families can apply for the Cambridge Preschool Program spring match for free public preschool and participating community-based providers.",
            action_label="Open CPP Application Info",
            url=CPP_URL,
            cost="Free",
            pathways=["students_families", "just_browsing"],
            source="Cambridge Office of Early Childhood",
        )
    ]


def parse_school_committee_meetings(limit: int = 6) -> list[FeedItem]:
    html = fetch_html(SCHOOL_COMMITTEE_URL)
    events = extract_school_calendar_events(html)
    items: list[FeedItem] = []
    seen: set[tuple[str, str]] = set()
    useful_keywords = (
        "committee",
        "subcommittee",
        "budget",
        "no school",
        "early release",
        "workshop",
        "meeting",
    )

    for event in events:
        current_date = event["date"]
        if not isinstance(current_date, datetime):
            continue

        title = clean_whitespace(str(event["title"]))
        lowered = clean_whitespace(f"{title} {event['location']}").lower()
        if not any(keyword in lowered for keyword in useful_keywords):
            continue
        if current_date.date() < TODAY.date():
            continue
        if not should_keep_dated_item(current_date):
            continue

        key = (title, current_date.strftime("%Y-%m-%d"))
        if key in seen:
            continue
        seen.add(key)

        location = clean_whitespace(str(event["location"]))

        description = "Upcoming Cambridge Public Schools event."
        if "committee" in lowered or "subcommittee" in lowered or "budget" in lowered:
            description = "Upcoming Cambridge Public Schools committee meeting or workshop."
        elif "no school" in lowered or "early release" in lowered:
            description = "Upcoming Cambridge Public Schools schedule change."

        items.append(
            FeedItem(
                title=title_case(title),
                date=iso_date(current_date),
                display_date=display_date(current_date),
                time=event["time"] if isinstance(event["time"], str) else None,
                location=location,
                description=description,
                action_label="Open CPS Calendar",
                url=SCHOOL_COMMITTEE_URL,
                cost="Free",
                pathways=["students_families", "just_browsing"],
                source="CPSD All Events",
            )
        )

        if len(items) >= limit:
            break

    return items


def parse_crls_calendar(limit: int = 5) -> list[FeedItem]:
    html = fetch_html(SCHOOL_COMMITTEE_URL)
    events = extract_school_calendar_events(html)
    items: list[FeedItem] = []
    seen: set[tuple[str, str]] = set()
    crls_keywords = (
        "crls",
        "cambridge rindge and latin",
        "student",
        "students",
        "yearbook",
        "college",
        "media cafe",
        "purim",
        "attles",
        "theater",
        "theatre",
        "music",
        "concert",
        "show",
        "celebration",
        "prom",
        "graduation",
    )

    for event in events:
        current_date = event["date"]
        if not isinstance(current_date, datetime):
            continue
        if current_date.date() < TODAY.date() or not should_keep_dated_item(current_date):
            continue

        title = clean_whitespace(str(event["title"]))
        location = clean_whitespace(str(event["location"]))
        haystack = f"{title} {location}".lower()
        if not contains_any_keyword(haystack, crls_keywords):
            continue

        key = (title, current_date.strftime("%Y-%m-%d"))
        if key in seen:
            continue
        seen.add(key)

        pathways = ["students_families", "just_browsing"]
        if contains_any_keyword(haystack, ("music", "concert", "theater", "theatre", "show", "celebration", "media arts", "arts")):
            pathways.append("arts")

        items.append(
            FeedItem(
                title=title_case(title),
                date=iso_date(current_date),
                display_date=display_date(current_date),
                time=event["time"] if isinstance(event["time"], str) else None,
                location=location,
                description="Upcoming Cambridge Rindge and Latin School or student-focused event.",
                action_label="Open CPS Calendar",
                url=SCHOOL_COMMITTEE_URL,
                cost="Free",
                pathways=sorted(set(pathways)),
                source="CRLS / CPS Calendar",
            )
        )

        if len(items) >= limit:
            break

    return items


def parse_crls_calendar_fallback(limit: int = 6) -> list[FeedItem]:
    return []


def parse_rwinters_entries(url: str, title_prefix: str, limit: int = 5) -> list[FeedItem]:
    html = fetch_html(url)
    parser = SimpleHTML()
    parser.feed(html)
    lines = parser.text_lines()

    items: list[FeedItem] = []
    date_pattern = re.compile(r"(\d{1,2}/\d{1,2}/\d{4})")
    time_pattern = re.compile(r"(\d{1,2}:\d{2}\s*[AP]M)", re.IGNORECASE)

    for line in lines:
        date_match = date_pattern.search(line)
        if not date_match:
            continue

        event_date = parse_numeric_date(date_match.group(1))
        if not should_keep_dated_item(event_date):
            continue

        title = clean_whitespace(date_pattern.sub("", line))
        time_text = None
        time_match = time_pattern.search(title)
        if time_match:
            time_text = time_match.group(1).upper()
            title = clean_whitespace(time_pattern.sub("", title))

        if len(title) < 8:
            continue

        lowered_title = title.lower()
        pathways = ["voting_civics", "just_browsing"]
        if any(
            keyword in lowered_title
            for keyword in (
                "artist",
                "artists",
                "art",
                "arts",
                "mural",
                "public space",
                "park",
                "renovation",
                "renovations",
            )
        ):
            pathways.append("arts")

        items.append(
            FeedItem(
                title=title_case(f"{title_prefix} {title}".strip()),
                date=iso_date(event_date),
                display_date=display_date(event_date),
                time=time_text,
                location="Cambridge",
                description=first_sentence(title) or "Upcoming civic meeting or deadline.",
                action_label="Open Source Page",
                url=url,
                cost="Free",
                pathways=sorted(set(pathways)),
                source="Robert Winters",
            )
        )

        if len(items) >= limit:
            break

    return items


def parse_primegov_portal() -> list[FeedItem]:
    html = fetch_html(PRIMEGOV_URL)
    parser = SimpleHTML()
    parser.feed(html)
    text = "\n".join(parser.text_lines())

    if "Current And Upcoming Meetings" not in text and "Current and Upcoming Meetings" not in text:
        return []

    return [
        FeedItem(
            title="Check Current and Upcoming Cambridge Public Meetings",
            date=None,
            display_date="Ongoing",
            time=None,
            location="Online",
            description="PrimeGov lists current and upcoming Cambridge public meetings, agendas, and documents.",
            action_label="Open Public Meetings Portal",
            url=PRIMEGOV_URL,
            cost="Free",
            pathways=["voting_civics", "just_browsing"],
            source="PrimeGov Public Portal",
        )
    ]


def parse_housing_opportunities() -> list[FeedItem]:
    sources = [
        ("for applicants", HOUSING_APPLICANTS_URL),
        ("rental applicant pool", RENTAL_POOL_URL),
        ("homeownership resale pool", RESALE_POOL_URL),
        ("middle-income rental program", MIDDLE_INCOME_URL),
    ]

    pages: dict[str, str] = {}
    for label, url in sources:
        html = fetch_html(url)
        parser = SimpleHTML()
        parser.feed(html)
        pages[label] = "\n".join(parser.text_lines())

    applicants_text = pages["for applicants"]
    rental_text = pages["rental applicant pool"]
    resale_text = pages["homeownership resale pool"]
    middle_income_text = pages["middle-income rental program"]

    items: list[FeedItem] = []

    if "Rental Applicant Pool" in rental_text and "accepting new Rental Applicant Pool preliminary applications" in rental_text:
        items.append(
            FeedItem(
                title="Apply to Cambridge Rental Applicant Pool",
                date=None,
                display_date="Applications accepted on a rolling basis",
                time=None,
                location="Cambridge Housing Department",
                description="Cambridge is accepting new preliminary applications for affordable inclusionary rental housing through the Rental Applicant Pool.",
                action_label="Open Rental Applicant Pool",
                url=RENTAL_POOL_URL,
                cost="Free",
                pathways=["housing", "just_browsing"],
                source="Cambridge Housing Department",
            )
        )

    if "Middle-Income Rental Program" in middle_income_text and "accepting applications" in middle_income_text:
        items.append(
            FeedItem(
                title="Join Cambridge Middle-Income Rental Waiting Pool",
                date=None,
                display_date="Applications accepted on a rolling basis",
                time=None,
                location="Cambridge Housing Department",
                description="Cambridge is accepting applications for its Middle-Income Rental Program waiting pool for affordable apartments.",
                action_label="Open Middle-Income Rental Program",
                url=MIDDLE_INCOME_URL,
                cost="Free",
                pathways=["housing", "just_browsing"],
                source="Cambridge Housing Department",
            )
        )

    if "Homeownership Resale Pool" in resale_text and "Applications are accepted on a rolling basis" in resale_text:
        items.append(
            FeedItem(
                title="Apply to Cambridge Homeownership Resale Pool",
                date=None,
                display_date="Applications accepted on a rolling basis",
                time=None,
                location="Cambridge Housing Department",
                description="Cambridge accepts rolling applications for affordable homeownership units offered through the Homeownership Resale Pool.",
                action_label="Open Resale Pool",
                url=RESALE_POOL_URL,
                cost="Free",
                pathways=["housing", "just_browsing"],
                source="Cambridge Housing Department",
            )
        )

    if "Newly developed units" in resale_text and "lottery opportunities" in resale_text:
        items.append(
            FeedItem(
                title="Track Cambridge Affordable Homeownership Lotteries",
                date=None,
                display_date="Sign up for future lottery notices",
                time=None,
                location="Cambridge Housing Department",
                description="New affordable homeownership units are sold through separate application processes and lotteries when they become available.",
                action_label="Open Housing Applicant Information",
                url=HOUSING_APPLICANTS_URL,
                cost="Free",
                pathways=["housing", "just_browsing"],
                source="Cambridge Housing Department",
            )
        )

    if not items and "Apply for Rental Housing" in applicants_text:
        items.append(
            FeedItem(
                title="Check Cambridge Affordable Housing Applicant Programs",
                date=None,
                display_date="Ongoing",
                time=None,
                location="Cambridge Housing Department",
                description="Cambridge lists current affordable rental and ownership applicant programs through its Housing Department.",
                action_label="Open Housing Applicant Information",
                url=HOUSING_APPLICANTS_URL,
                cost="Free",
                pathways=["housing", "just_browsing"],
                source="Cambridge Housing Department",
            )
        )

    return items


def parse_housing_trust_meetings(limit: int = 3) -> list[FeedItem]:
    html = fetch_html(HOUSING_TRUST_URL)
    parser = SimpleHTML()
    parser.feed(html)
    text = "\n".join(parser.text_lines())

    items: list[FeedItem] = []
    pattern = re.compile(
        r"([A-Z][a-z]+ \d{1,2}, \d{4}) Register here to watch\.",
        re.IGNORECASE,
    )

    for match in pattern.finditer(text):
        meeting_date = parse_month_day_year(match.group(1))
        if meeting_date is None or meeting_date.date() < TODAY.date():
            continue
        if not should_keep_dated_item(meeting_date):
            continue

        items.append(
            FeedItem(
                title="Cambridge Affordable Housing Trust Meeting",
                date=iso_date(meeting_date),
                display_date=display_date(meeting_date),
                time="4:00 PM",
                location="Ackermann Room at City Hall or webinar",
                description="Monthly Affordable Housing Trust meeting covering affordable housing policy, funding, and development in Cambridge.",
                action_label="Open Housing Trust Page",
                url=HOUSING_TRUST_URL,
                cost="Free",
                pathways=["housing", "just_browsing"],
                source="Cambridge Housing Department",
            )
        )

        if len(items) >= limit:
            break

    return items


def classify_library_pathways(title: str, description: str) -> list[str]:
    haystack = f"{title} {description}".lower()
    pathways = ["just_browsing"]

    if contains_any_keyword(haystack, ("teen", "kids", "children", "youth", "family", "families")):
        pathways.append("students_families")

    if contains_any_keyword(haystack, ("older adult", "aging", "mindfulness", "seniors")):
        pathways.append("older_adults")

    if contains_any_keyword(
        haystack,
        (
            "art",
            "arts",
            "artist",
            "artists",
            "craft",
            "crafts",
            "exhibit",
            "exhibition",
            "poetry",
            "performance",
            "mural",
            "music",
            "film",
            "dance",
            "theater",
            "theatre",
            "studio",
            "writing",
            "maker",
        ),
    ):
        pathways.append("arts")

    if contains_any_keyword(
        haystack,
        (
            "job",
            "esol",
            "tech help",
            "digital equity",
            "social worker",
            "tax assistance",
            "resume",
            "career",
            "literacy",
        ),
    ):
        pathways.append("education_work")

    return sorted(set(pathways))


def useful_library_program(title: str, description: str) -> bool:
    haystack = f"{title} {description}".lower()
    drop_keywords = [
        "songs, stories and play",
        "baby lapsit",
        "morning sing-along",
        "lego time",
        "story time",
        "storytime",
        "sing-along",
    ]
    if any(keyword in haystack for keyword in drop_keywords):
        return False

    keep_keywords = [
        "tutoring",
        "esol",
        "job",
        "social worker",
        "shop for free",
        "tech help",
        "digital equity",
        "tax assistance",
        "literacy",
        "hive safety training",
        "apply",
        "registration is required",
        "deadline",
        "application",
        "artist",
        "art",
        "craft",
        "poetry",
        "performance",
        "film",
        "music",
        "dance",
        "theater",
        "theatre",
        "writing",
        "workshop",
        "makerspace",
        "vinyl cutting",
        "author",
        "creative coding",
        "steam academy",
    ]
    return contains_any_keyword(haystack, keep_keywords)


def parse_library_programs(limit: int = 4) -> list[FeedItem]:
    items: list[FeedItem] = []
    seen: set[tuple[str, str]] = set()
    month_html = fetch_html(LIBRARY_CALENDAR_URL)
    months_to_fetch = current_and_future_months(extract_library_month_options(month_html), max_months=8)
    if not months_to_fetch:
        months_to_fetch = [(TODAY.year, TODAY.month)]

    for year, month in months_to_fetch:
        html = month_html if (year, month) == months_to_fetch[0] else fetch_html(build_library_month_url(year, month))
        parser = SimpleHTML()
        parser.feed(html)
        lines = parser.text_lines()
        title_to_url = {
            clean_whitespace(link["text"]): event_detail_url(LIBRARY_CALENDAR_URL, link["href"])
            for link in parser.links
            if clean_whitespace(link["text"])
        }

        try:
            start = next(i for i, line in enumerate(lines) if line.startswith("Displaying "))
        except StopIteration:
            continue

        calendar_lines: list[str] = []
        for line in lines[start + 1 :]:
            if line in {"Select A Topic", "Filter Calendar", "View Today"}:
                break
            calendar_lines.append(line)

        current_date: datetime | None = None
        i = 0
        while i < len(calendar_lines):
            line = calendar_lines[i]
            if (
                i + 1 < len(calendar_lines)
                and re.match(r"^[A-Z][a-z]+ \d{1,2}$", line)
                and re.match(
                    r"^(Sunday|Monday|Tuesday|Wednesday|Thursday|Friday|Saturday)$",
                    calendar_lines[i + 1],
                )
            ):
                try:
                    current_date = datetime.strptime(
                        f"{line} {year}",
                        "%B %d %Y",
                    )
                except ValueError:
                    current_date = None
                i += 2
                continue

            if current_date is None or i + 1 >= len(calendar_lines):
                i += 1
                continue

            time_line = calendar_lines[i]
            if not re.match(r"^\d{1,2}:\d{2}\s*[AP]M$", time_line, re.IGNORECASE):
                i += 1
                continue

            title = clean_whitespace(calendar_lines[i + 1])
            j = i + 2
            location = "Cambridge Public Library"
            description_parts: list[str] = []

            if j < len(calendar_lines):
                candidate = calendar_lines[j]
                looks_like_location = (
                    "branch" in candidate.lower()
                    or "library" in candidate.lower()
                    or "cambridge, ma" in candidate.lower()
                    or candidate.lower() == "virtual"
                    or "room" in candidate.lower()
                )
                if looks_like_location:
                    location = candidate
                    j += 1

            while j < len(calendar_lines):
                next_line = calendar_lines[j]
                if re.match(r"^[A-Z][a-z]+ \d{1,2}$", next_line):
                    break
                if re.match(r"^\d{1,2}:\d{2}\s*[AP]M$", next_line, re.IGNORECASE):
                    break
                description_parts.append(next_line)
                j += 1

            description = clean_whitespace(" ".join(description_parts))
            i = j

            if not title or "cancel" in title.lower():
                continue
            if current_date.date() < TODAY.date():
                continue
            if not useful_library_program(title, description):
                continue
            if not should_keep_dated_item(current_date):
                continue

            key = (title, current_date.strftime("%Y-%m-%d"))
            if key in seen:
                continue
            seen.add(key)

            items.append(
                FeedItem(
                    title=title.replace("[CANCELED] ", "").replace("CANCELLED- ", "").strip(),
                    date=iso_date(current_date),
                    display_date=display_date(current_date),
                    time=time_line.upper(),
                    location=location,
                    description=first_sentence(description) or "Upcoming Cambridge Public Library program.",
                    action_label="View Program Details",
                    url=title_to_url.get(title, LIBRARY_CALENDAR_URL),
                    cost="Free",
                    pathways=classify_library_pathways(title, description),
                    source="Cambridge Public Library",
                )
            )

            if len(items) >= limit:
                return items

    return items


def parse_cambridge_arts() -> list[FeedItem]:
    html = fetch_html(CAMBRIDGE_ARTS_URL)
    parser = SimpleHTML()
    parser.feed(html)
    text = "\n".join(parser.text_lines())

    if "View Events Calendar" not in text and "Upcoming Events" not in text:
        return []

    return [
        FeedItem(
            title="Browse Cambridge Arts Events",
            date=None,
            display_date="Ongoing",
            time=None,
            location="Cambridge",
            description="Cambridge Arts publishes a local arts and culture calendar, festival information, and public art resources.",
            action_label="Open Arts Calendar",
            url=CAMBRIDGE_ARTS_CALENDAR_URL,
            cost="Free",
            pathways=["arts", "just_browsing"],
            source="Cambridge Arts",
        )
    ]


def parse_boston_dog_license() -> list[FeedItem]:
    html = fetch_html(BOSTON_DOG_LICENSE_URL)
    parser = SimpleHTML()
    parser.feed(html)
    text = "\n".join(parser.text_lines())

    deadline = None
    full_match = re.search(r"\b([A-Z][a-z]+ \d{1,2}, \d{4})\b", text)
    if full_match and "license" in text.lower():
        deadline = parse_month_day_year(full_match.group(1))
    if deadline is None:
        month_day_match = re.search(r"by ([A-Z][a-z]+ \d{1,2})", text, re.IGNORECASE)
        if month_day_match:
            deadline = parse_month_day(month_day_match.group(1))
    if deadline is None:
        march_match = re.search(r"\b(March 31)\b", text, re.IGNORECASE)
        if march_match:
            deadline = parse_month_day(march_match.group(1))

    if deadline is None or not should_keep_dated_item(deadline):
        return []

    return [
        FeedItem(
            title="License Your Dog in Boston",
            date=iso_date(deadline),
            display_date=display_date(deadline),
            time=None,
            location="Boston Animal Care and Control",
            description="Dog owners must license dogs older than six months before the annual City deadline.",
            action_label="License Your Dog",
            url=BOSTON_DOG_LICENSE_URL,
            cost="$15-$30, waived for some eligible older adults applying by mail or in person",
            pathways=["renewals", "older_adults"],
            source="City of Boston Animal Care and Control",
        )
    ]


def parse_boston_tax_exemptions() -> list[FeedItem]:
    html = fetch_html(BOSTON_TAX_EXEMPTIONS_URL)
    parser = SimpleHTML()
    parser.feed(html)
    text = "\n".join(parser.text_lines())

    deadline = None
    full_match = re.search(r"\b([A-Z][a-z]+ \d{1,2}, \d{4})\b", text)
    if full_match:
        deadline = parse_month_day_year(full_match.group(1))
    if deadline is None:
        month_day_match = re.search(r"(?:due|deadline)[^\n.]*?\b([A-Z][a-z]+ \d{1,2})\b", text, re.IGNORECASE)
        if month_day_match:
            deadline = parse_month_day(month_day_match.group(1))
    if deadline is None and "April 1" in text:
        deadline = parse_month_day("April 1")

    if deadline is None or not should_keep_dated_item(deadline):
        return []

    return [
        FeedItem(
            title="Apply for Boston Property Tax Exemptions",
            date=iso_date(deadline),
            display_date=display_date(deadline),
            time=None,
            location="Boston Assessing Department",
            description="Eligible homeowners can file for the current fiscal year property tax exemption by the annual deadline.",
            action_label="Open Tax Exemptions",
            url=BOSTON_TAX_EXEMPTIONS_URL,
            cost="Free",
            pathways=["renewals", "housing", "older_adults"],
            source="City of Boston Assessing",
        )
    ]


def parse_boston_elections() -> list[FeedItem]:
    html = fetch_html(BOSTON_ELECTIONS_URL)
    parser = SimpleHTML()
    parser.feed(html)
    text = "\n".join(parser.text_lines())

    if "Annual Census" not in text and "Annual Listing" not in text:
        return []

    return [
        FeedItem(
            title="Complete Your Boston Annual Census",
            date=None,
            display_date="Return as soon as possible",
            time=None,
            location="Online or by mail",
            description="Boston residents should complete the annual census or listing to help keep voter rolls current and support city services.",
            action_label="Open Elections Commission Page",
            url=BOSTON_ELECTIONS_URL,
            cost="Free",
            pathways=["voting_civics", "renewals", "just_browsing"],
            source="Boston Elections Commission",
        )
    ]


def parse_boston_housing() -> list[FeedItem]:
    html = fetch_html(BOSTON_HOUSING_URL)
    parser = SimpleHTML()
    parser.feed(html)
    text = "\n".join(parser.text_lines())

    if "Metrolist" not in text and "income-restricted housing" not in text:
        return []

    return [
        FeedItem(
            title="Track Boston Affordable Housing Lotteries",
            date=None,
            display_date="Ongoing",
            time=None,
            location="Boston",
            description="Boston's Office of Housing points residents to Metrolist for income-restricted rentals, homeownership opportunities, and housing lottery listings.",
            action_label="Open Boston Housing",
            url=BOSTON_HOUSING_URL,
            cost="Free",
            pathways=["housing", "just_browsing"],
            source="City of Boston Housing",
        )
    ]


def parse_boston_age_strong() -> list[FeedItem]:
    html = fetch_html(BOSTON_AGE_STRONG_GRANTS_URL)
    parser = SimpleHTML()
    parser.feed(html)
    text = "\n".join(parser.text_lines())

    if "Rolling Grant" not in text and "Rolling Grants" not in text:
        return []

    dates = []
    for match in re.findall(r"\b([A-Z][a-z]+ \d{1,2}, \d{4})\b", text):
        parsed = parse_month_day_year(match)
        if parsed is None or not should_keep_dated_item(parsed):
            continue
        if parsed.date() < TODAY.date():
            continue
        dates.append(parsed)

    if dates:
        deadline = min(dates)
        return [
            FeedItem(
                title="Apply for an Age Strong Rolling Grant",
                date=iso_date(deadline),
                display_date=display_date(deadline),
                time="11:59 PM",
                location="Age Strong Commission",
                description="Boston nonprofits, civic associations, and senior groups can apply for small-scale funding that supports older adult programming and social connection.",
                action_label="Open Age Strong Grants",
                url=BOSTON_AGE_STRONG_GRANTS_URL,
                cost="Free to apply",
                pathways=["older_adults", "health", "just_browsing"],
                source="City of Boston Age Strong Commission",
            )
        ]

    return [
        FeedItem(
            title="Explore Age Strong Grant Opportunities",
            date=None,
            display_date="Ongoing",
            time=None,
            location="Boston",
            description="Age Strong offers grant opportunities for organizations serving older adults in Boston, including small-scale rolling support.",
            action_label="Open Age Strong Grants",
            url=BOSTON_AGE_STRONG_GRANTS_URL,
            cost="Free to apply",
            pathways=["older_adults", "health", "just_browsing"],
            source="City of Boston Age Strong Commission",
        )
    ]


def parse_boston_bps_enrollment() -> list[FeedItem]:
    html = fetch_html(BOSTON_BPS_ENROLL_URL)
    parser = SimpleHTML()
    parser.feed(html)
    text = "\n".join(parser.text_lines())

    if "Welcome to BPS Enrollment" not in text:
        return []

    matches = re.findall(r"\b([A-Z][a-z]+ \d{1,2}, \d{4})\b", text)
    future_dates = []
    for match in matches:
        parsed = parse_month_day_year(match)
        if parsed is None or parsed.date() < TODAY.date():
            continue
        if should_keep_dated_item(parsed):
            future_dates.append(parsed)

    if future_dates:
        deadline = min(future_dates)
        return [
            FeedItem(
                title="Review Boston Public Schools Enrollment Deadlines",
                date=iso_date(deadline),
                display_date=display_date(deadline),
                time=None,
                location="Boston Public Schools",
                description="Boston families can review current school enrollment dates, registration windows, and assignment steps through the BPS enrollment page.",
                action_label="Open BPS Enrollment",
                url=BOSTON_BPS_ENROLL_URL,
                cost="Free",
                pathways=["students_families", "education_work", "just_browsing"],
                source="Boston Public Schools",
            )
        ]

    return [
        FeedItem(
            title="Check Boston Public Schools Enrollment",
            date=None,
            display_date="Ongoing",
            time=None,
            location="Boston Public Schools",
            description="Boston families can use the BPS enrollment page to review registration steps, school assignment information, and current enrollment guidance.",
            action_label="Open BPS Enrollment",
            url=BOSTON_BPS_ENROLL_URL,
            cost="Free",
            pathways=["students_families", "education_work", "just_browsing"],
            source="Boston Public Schools",
        )
    ]


def parse_boston_bpl_esol() -> list[FeedItem]:
    html = fetch_html(BOSTON_BPL_ESOL_URL)
    parser = SimpleHTML()
    parser.feed(html)
    text = "\n".join(parser.text_lines())

    if "English Language Learning" not in text and "English for Speakers of Other Languages" not in text:
        return []

    return [
        FeedItem(
            title="Join Boston Public Library ESOL Classes",
            date=None,
            display_date="Ongoing",
            time=None,
            location="Boston Public Library",
            description="Boston Public Library offers free English-language learning classes and conversation groups for adult learners living in Massachusetts.",
            action_label="Open BPL ESOL",
            url=BOSTON_BPL_ESOL_URL,
            cost="Free",
            pathways=["education_work", "just_browsing"],
            source="Boston Public Library",
        )
    ]


def build_feed() -> dict:
    parser_by_label = {
        "parking permits": parse_parking_deadline,
        "tax exemptions": parse_tax_exemptions,
        "annual census": parse_census_notice,
        "public health": parse_public_health_minigrants,
        "cpp": parse_cpp_application,
        "housing opportunities": parse_housing_opportunities,
        "housing trust meetings": parse_housing_trust_meetings,
        "school committee": parse_school_committee_meetings,
        "crls calendar": parse_crls_calendar,
        "primegov portal": parse_primegov_portal,
        "library programs": parse_library_programs,
        "cambridge arts": parse_cambridge_arts,
    }

    items: list[FeedItem] = []
    errors: list[str] = []

    for label, _group, _summary, _source_type, _notes in SOURCE_PARSERS:
        parser = parser_by_label[label]
        try:
            items.extend(parser())
        except Exception as exc:
            errors.append(f"{label}: {exc}")

    unique = dedupe_items(items)
    ordered = sorted(unique, key=sort_key)
    return {
        "items": [item.to_dict() for item in ordered],
        "metadata": {
            "updated_at": datetime.now().isoformat(timespec="seconds"),
            "source_errors": errors,
        },
    }


def build_boston_feed() -> dict:
    parser_by_label = {
        "boston dog license": parse_boston_dog_license,
        "boston tax exemptions": parse_boston_tax_exemptions,
        "boston elections": parse_boston_elections,
        "boston housing": parse_boston_housing,
        "boston age strong": parse_boston_age_strong,
        "boston bps enrollment": parse_boston_bps_enrollment,
        "boston bpl esol": parse_boston_bpl_esol,
    }

    items: list[FeedItem] = []
    errors: list[str] = []

    for label, _group, _summary, _source_type, _notes in BOSTON_SOURCE_PARSERS:
        parser = parser_by_label[label]
        try:
            items.extend(parser())
        except Exception as exc:
            errors.append(f"{label}: {exc}")

    unique = dedupe_items(items)
    ordered = sorted(unique, key=sort_key)
    return {
        "items": [item.to_dict() for item in ordered],
        "metadata": {
            "updated_at": datetime.now().isoformat(timespec="seconds"),
            "source_errors": errors,
        },
    }


def main() -> None:
    cambridge_feed = build_feed()
    boston_feed = build_boston_feed()

    FEEDS_DIR.mkdir(exist_ok=True)
    FEED_PATH.write_text(json.dumps(cambridge_feed, indent=2), encoding="utf-8")
    BOSTON_FEED_PATH.write_text(json.dumps(boston_feed, indent=2), encoding="utf-8")

    print(f"Wrote {len(cambridge_feed['items'])} items to {FEED_PATH.name}")
    print(f"Wrote {len(boston_feed['items'])} items to {BOSTON_FEED_PATH.relative_to(PROJECT_ROOT)}")

    for label, feed in (("Cambridge", cambridge_feed), ("Boston", boston_feed)):
        errors = feed.get("metadata", {}).get("source_errors", [])
        if errors:
            print(f"{label} warnings:")
            for error in errors:
                print(f"- {error}")


if __name__ == "__main__":
    main()
