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
TODAY = datetime.now()
CURRENT_YEAR = TODAY.year
LOOKAHEAD_DAYS = 365
PASSED_ARCHIVE_DAYS = 60


PARKING_URL = "https://www.cambridgema.gov/en/iwantto/applyforaparkingpermit"
EXEMPTIONS_URL = "https://www.cambridgema.gov/Services/taxpayerexemptions"
CENSUS_URL = "https://www.cambridgema.gov/Departments/electioncommission/news/2026/03/2026annualcitycensus"
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
    html = fetch_html(CENSUS_URL)
    parser = SimpleHTML()
    parser.feed(html)
    text = "\n".join(parser.text_lines())

    if "Annual City Census" not in text:
        raise ValueError("Could not find annual census notice.")

    return [
        FeedItem(
            title="Return Your Cambridge Annual City Census",
            date=None,
            display_date="Return as soon as possible",
            time=None,
            location="Online, mail, or drop box",
            description="Cambridge residents should return the annual city census to protect voting rights and support municipal services.",
            action_label="Open Census Form",
            url="https://www.cambridgema.gov/census",
            cost="Free",
            pathways=["voting_civics", "renewals", "just_browsing"],
            source="Cambridge Election Commission",
        )
    ]


def parse_school_committee_meetings(limit: int = 6) -> list[FeedItem]:
    html = fetch_html(SCHOOL_COMMITTEE_URL)
    parser = SimpleHTML()
    parser.feed(html)
    lines = parser.text_lines()

    items: list[FeedItem] = []
    seen: set[tuple[str, str]] = set()
    current_date: datetime | None = None
    date_pattern = re.compile(
        r"^(Sunday|Monday|Tuesday|Wednesday|Thursday|Friday|Saturday),\s+([A-Z][a-z]+)\s+(\d{1,2})$"
    )
    time_pattern = re.compile(r"^(\d{1,2}:\d{2}\s*[AP]M)(?:\s*-\s*\d{1,2}:\d{2}\s*[AP]M)?$", re.IGNORECASE)
    useful_keywords = (
        "committee",
        "subcommittee",
        "budget",
        "no school",
        "early release",
        "workshop",
        "meeting",
    )
    stop_lines = {
        "Print Grid Element",
        "Calendar RSS Feeds",
        "Powered by Finalsite",
    }

    for i, line in enumerate(lines):
        if line in stop_lines:
            continue

        date_match = date_pattern.match(line)
        if date_match:
            month_name = date_match.group(2)
            day = int(date_match.group(3))
            event_year = TODAY.year
            if month_name == "January" and TODAY.month == 12:
                event_year += 1
            current_date = datetime.strptime(f"{month_name} {day} {event_year}", "%B %d %Y")
            continue

        if current_date is None or i + 1 >= len(lines):
            continue

        next_line = lines[i + 1]
        if not time_pattern.match(next_line) and next_line != "All Day":
            continue

        title = clean_whitespace(line)
        lowered = title.lower()
        if not any(keyword in lowered for keyword in useful_keywords):
            continue
        if "cancel" in lowered:
            continue
        if current_date.date() < TODAY.date():
            continue
        if not should_keep_dated_item(current_date):
            continue

        key = (title, current_date.strftime("%Y-%m-%d"))
        if key in seen:
            continue
        seen.add(key)

        location = "Cambridge Public Schools"
        if i + 2 < len(lines):
            candidate = clean_whitespace(lines[i + 2])
            if candidate and not date_pattern.match(candidate) and not time_pattern.match(candidate):
                location = candidate

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
                time=None if next_line == "All Day" else parse_time_range_start(next_line),
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
    items: list[FeedItem] = []
    for item in parse_school_committee_meetings(limit=24):
        title = item.title.lower()
        location = item.location.lower()
        description = item.description.lower()

        if not any(
            keyword in " ".join([title, location, description])
            for keyword in (
                "crls",
                "yearbook",
                "college",
                "student",
                "purim",
                "media cafe",
                "meeting room at crls",
                "budget",
            )
        ):
            continue

        normalized_source = "CRLS / CPS Calendar"
        action_label = "Open CPS Calendar"
        pathways = ["students_families", "just_browsing"]

        items.append(
            FeedItem(
                title=item.title,
                date=item.date,
                display_date=item.display_date,
                time=item.time,
                location=item.location,
                description=item.description,
                action_label=action_label,
                url=item.url,
                cost=item.cost,
                pathways=pathways,
                source=normalized_source,
            )
        )

        if len(items) >= limit:
            break

    return items


def parse_crls_calendar_fallback(limit: int = 6) -> list[FeedItem]:
    return parse_crls_calendar(limit=limit)


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

    if any(word in haystack for word in ("teen", "kids", "children", "youth")):
        pathways.append("students_families")

    if any(word in haystack for word in ("older adult", "aging", "mindfulness")):
        pathways.append("older_adults")

    if any(
        word in haystack
        for word in (
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
        )
    ):
        pathways.append("arts")

    if any(
        re.search(pattern, haystack)
        for pattern in (
            r"\bjob\b",
            r"\besol\b",
            r"\btech help\b",
            r"\bdigital equity\b",
            r"\bsocial worker\b",
            r"\btax assistance\b",
        )
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
    ]
    return any(keyword in haystack for keyword in keep_keywords)


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


def build_feed() -> dict:
    source_parsers = [
        ("parking permits", parse_parking_deadline),
        ("tax exemptions", parse_tax_exemptions),
        ("annual census", parse_census_notice),
        ("housing opportunities", parse_housing_opportunities),
        ("housing trust meetings", parse_housing_trust_meetings),
        ("school committee", parse_school_committee_meetings),
        ("crls calendar", parse_crls_calendar),
        ("crls calendar fallback", parse_crls_calendar_fallback),
        ("primegov portal", parse_primegov_portal),
        ("library programs", parse_library_programs),
    ]

    items: list[FeedItem] = []
    errors: list[str] = []

    for label, parser in source_parsers:
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
    feed = build_feed()
    FEED_PATH.write_text(json.dumps(feed, indent=2), encoding="utf-8")
    print(f"Wrote {len(feed['items'])} items to {FEED_PATH.name}")
    errors = feed.get("metadata", {}).get("source_errors", [])
    if errors:
        print("Warnings:")
        for error in errors:
            print(f"- {error}")


if __name__ == "__main__":
    main()
