from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime
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


PARKING_URL = "https://www.cambridgema.gov/en/iwantto/applyforaparkingpermit"
EXEMPTIONS_URL = "https://www.cambridgema.gov/Services/taxpayerexemptions"
CENSUS_URL = "https://www.cambridgema.gov/Departments/electioncommission/news/2026/03/2026annualcitycensus"
LIBRARY_URL = "https://www.cambridgema.gov/en/Departments/cambridgepubliclibrary/"


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
    return value.date() >= TODAY.date()


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
            pathways=["renewals", "older_adult_support"],
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
            pathways=["voting_help", "renewals", "just_browsing"],
        )
    ]


def classify_library_pathways(title: str, description: str) -> list[str]:
    haystack = f"{title} {description}".lower()
    pathways = ["just_browsing"]

    if any(word in haystack for word in ("teen", "kids", "children", "youth")):
        pathways.append("kids_teens")

    if any(word in haystack for word in ("older adult", "aging", "mindfulness")):
        pathways.append("older_adult_support")

    if any(word in haystack for word in ("job", "esol", "tech", "social worker")):
        pathways.append("food_basics")

    return sorted(set(pathways))


def useful_library_program(title: str, description: str) -> bool:
    haystack = f"{title} {description}".lower()
    keep_keywords = [
        "tutoring",
        "esol",
        "job",
        "social worker",
        "shop for free",
        "older adult",
        "aging",
        "tech help",
        "youtube",
    ]
    return any(keyword in haystack for keyword in keep_keywords)


def parse_library_programs(limit: int = 4) -> list[FeedItem]:
    html = fetch_html(LIBRARY_URL)
    parser = SimpleHTML()
    parser.feed(html)
    lines = parser.text_lines()

    title_to_url: dict[str, str] = {}
    for link in parser.links:
        title = clean_whitespace(link["text"])
        if title:
            title_to_url[title] = urljoin(LIBRARY_URL, link["href"])

    try:
        start = lines.index("Coming Up") + 1
    except ValueError as exc:
        raise ValueError("Could not find library Coming Up section.") from exc

    section_lines: list[str] = []
    for line in lines[start:]:
        if line == "Contact CPL":
            break
        section_lines.append(line)

    items: list[FeedItem] = []
    month_day: str | None = None
    i = 0

    heading_pattern = re.compile(r"^[A-Z][a-z]{2} \d{1,2} [A-Z][a-z]{2}$")
    time_pattern = re.compile(r"^\d{1,2}:\d{2}\s*[AP]M$")

    while i < len(section_lines):
        line = section_lines[i]

        if heading_pattern.match(line):
            month_day = line
            i += 1
            continue

        if month_day and i + 1 < len(section_lines) and time_pattern.match(section_lines[i + 1]):
            title = line
            time_text = section_lines[i + 1].upper()
            description_parts: list[str] = []
            j = i + 2

            while j < len(section_lines):
                next_line = section_lines[j]
                if heading_pattern.match(next_line):
                    break
                if j + 1 < len(section_lines) and time_pattern.match(section_lines[j + 1]):
                    break
                description_parts.append(next_line)
                j += 1

            description = clean_whitespace(" ".join(description_parts))
            i = j

            if "cancel" in title.lower():
                continue

            if not useful_library_program(title, description):
                continue

            event_date = datetime.strptime(f"{month_day} {CURRENT_YEAR}", "%b %d %a %Y")
            if not should_keep_dated_item(event_date):
                continue

            date_iso = event_date.strftime("%Y-%m-%dT%H:%M:%S")
            display = event_date.strftime("%A, %B %-d, %Y")
            title_url = title_to_url.get(title, LIBRARY_URL)

            items.append(
                FeedItem(
                    title=title.replace("[CANCELED] ", "").replace("CANCELLED- ", "").strip(),
                    date=date_iso,
                    display_date=display,
                    time=time_text,
                    location="Cambridge Public Library",
                    description=first_sentence(description),
                    action_label="View Program Details",
                    url=title_url,
                    cost="Free",
                    pathways=classify_library_pathways(title, description),
                )
            )
            continue

        i += 1

    return items[:limit]


def build_feed() -> dict:
    source_parsers = [
        ("parking permits", parse_parking_deadline),
        ("tax exemptions", parse_tax_exemptions),
        ("annual census", parse_census_notice),
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
