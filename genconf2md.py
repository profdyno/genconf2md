#!/usr/bin/env python3
"""
GenConf2Markdown - Scrape LDS General Conference talks to Obsidian Markdown.
"""

import re
import sys
import json
import calendar
from datetime import date, timedelta

try:
    import requests
    from bs4 import BeautifulSoup, NavigableString, Tag
except ImportError:
    print("Required packages not installed. Run:")
    print("  pip install requests beautifulsoup4")
    sys.exit(1)


TITLES_TO_STRIP = [
    "President", "Elder", "Bishop", "Brother", "Sister",
    "Patriarch", "Apostle", "Prophet",
]


def get_first_saturday(year, month):
    """Get the first Saturday of the given month/year."""
    first_day = date(year, month, 1)
    diff = (5 - first_day.weekday()) % 7  # 5 = Saturday
    return first_day + timedelta(days=diff)


def strip_author_titles(name):
    """Remove ecclesiastical titles from a name."""
    name = name.strip()
    for title in TITLES_TO_STRIP:
        if name.startswith(title + " "):
            name = name[len(title):].strip()
    return name


def html_to_markdown(element):
    """Recursively convert an HTML element tree to markdown text."""
    if isinstance(element, NavigableString):
        return str(element)

    if not isinstance(element, Tag):
        return ""

    tag = element.name

    # Skip these elements entirely
    if tag in ("video", "audio", "script", "style"):
        return ""
    if tag == "span" and "page-break" in element.get("class", []):
        return ""

    # Collect children's markdown
    children_md = "".join(html_to_markdown(c) for c in element.children)

    if tag == "p":
        return children_md.strip() + "\n\n"

    if tag == "blockquote":
        lines = children_md.strip().split("\n")
        # Filter out empty lines between paragraphs, then re-add quote markers
        quoted_lines = []
        for line in lines:
            stripped = line.strip()
            if stripped:
                quoted_lines.append("> " + stripped)
            else:
                quoted_lines.append(">")
        return "\n".join(quoted_lines) + "\n\n"

    if tag in ("em", "i"):
        return "*" + children_md + "*"

    if tag in ("strong", "b"):
        return "**" + children_md + "**"

    if tag == "cite":
        return "*" + children_md + "*"

    if tag == "sup":
        return children_md

    if tag == "a":
        classes = element.get("class", [])
        if "note-ref" in classes:
            sup = element.find("sup")
            if sup:
                marker = sup.get("data-value", sup.get_text(strip=True))
                return f"[^{marker}]"
            return ""
        # Scripture or cross references
        href = element.get("href", "")
        if href and not href.startswith("http"):
            href = "https://www.churchofjesuschrist.org" + href
        link_text = children_md.strip()
        if href and link_text:
            return f"[{link_text}]({href})"
        return link_text

    if tag in ("h2", "h3", "h4"):
        level = int(tag[1])
        return "#" * level + " " + children_md.strip() + "\n\n"

    if tag == "ul":
        items = []
        for li in element.find_all("li", recursive=False):
            li_text = html_to_markdown(li).strip()
            items.append("- " + li_text)
        return "\n".join(items) + "\n\n"

    if tag == "ol":
        items = []
        for i, li in enumerate(element.find_all("li", recursive=False), 1):
            li_text = html_to_markdown(li).strip()
            items.append(f"{i}. " + li_text)
        return "\n".join(items) + "\n\n"

    # For div, span, li, header, section, footer, etc. — just pass through children
    return children_md


def format_footnotes(footnotes_data):
    """Convert footnotes JSON data to markdown footnote definitions."""
    if not footnotes_data:
        return ""

    entries = []
    # Sort by numeric portion of the key (note1, note2, ...)
    sorted_keys = sorted(
        footnotes_data.keys(),
        key=lambda k: int(re.search(r"\d+", k).group())
    )

    for note_id in sorted_keys:
        note = footnotes_data[note_id]
        marker = re.search(r"\d+", note_id).group()
        text_html = note.get("text", "")

        soup = BeautifulSoup(text_html, "html.parser")

        # Convert links inside footnotes
        for a_tag in soup.find_all("a"):
            href = a_tag.get("href", "")
            if href and not href.startswith("http"):
                href = "https://www.churchofjesuschrist.org" + href
            link_text = a_tag.get_text()
            a_tag.replace_with(f"[{link_text}]({href})")

        # Convert <cite> to italic
        for cite_tag in soup.find_all("cite"):
            cite_tag.replace_with(f"*{cite_tag.get_text()}*")

        text = soup.get_text().strip()
        entries.append(f"[^{marker}]: {text}")

    return "\n".join(entries)


def fetch_session_info(year, month_num, talk_uri, lang, headers):
    """Fetch the conference table of contents to find the session for a talk."""
    toc_uri = f"/general-conference/{year}/{month_num:02d}"
    toc_api_url = (
        "https://www.churchofjesuschrist.org/study/api/v3/language-pages/type/content"
        f"?lang={lang}&uri={toc_uri}"
    )
    try:
        resp = requests.get(toc_api_url, headers=headers)
        resp.raise_for_status()
        toc_html = resp.json().get("content", {}).get("body", "")
        toc_soup = BeautifulSoup(toc_html, "html.parser")

        # Find all session headings and their associated talk links
        current_session = ""
        for el in toc_soup.find_all(["h2", "h3", "a"]):
            if el.name in ("h2", "h3"):
                text = el.get_text(strip=True)
                if "session" in text.lower():
                    current_session = text
            elif el.name == "a":
                href = el.get("href", "")
                if talk_uri in href and current_session:
                    return current_session
    except Exception:
        pass
    return ""


def parse_session(session_name):
    """Extract day abbreviation and session time from a session name.

    E.g. 'Sunday Morning Session' -> ('Sun', 'Morning')
         'Saturday Afternoon Session' -> ('Sat', 'Afternoon')
         'Saturday Evening Session' -> ('Sat', 'Evening')
    """
    day_abbr = "Sat"  # default
    session_time = "Morning"  # default

    if not session_name:
        return day_abbr, session_time

    name_lower = session_name.lower()
    if "sunday" in name_lower:
        day_abbr = "Sun"
    elif "saturday" in name_lower:
        day_abbr = "Sat"

    for part in ("Morning", "Afternoon", "Evening"):
        if part.lower() in name_lower:
            session_time = part
            break

    return day_abbr, session_time


def scrape_talk(url):
    """Scrape a General Conference talk via the API and return extracted fields."""
    # Extract the URI path from the URL
    match = re.search(r"/study(/general-conference/\d{4}/\d{2}/[^?&#]+)", url)
    if not match:
        print("Error: Could not parse the URL.")
        print("Expected format: https://www.churchofjesuschrist.org/study/general-conference/YYYY/MM/talkname")
        sys.exit(1)

    uri = match.group(1)

    # Extract year and month from URI
    ym_match = re.search(r"/general-conference/(\d{4})/(\d{2})/", uri)
    year = int(ym_match.group(1))
    month_num = int(ym_match.group(2))

    # Determine language from URL, default to eng
    lang_match = re.search(r"lang=(\w+)", url)
    lang = lang_match.group(1) if lang_match else "eng"

    # Fetch from the content API
    api_url = (
        "https://www.churchofjesuschrist.org/study/api/v3/language-pages/type/content"
        f"?lang={lang}&uri={uri}"
    )
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        )
    }

    response = requests.get(api_url, headers=headers)
    response.raise_for_status()
    data = response.json()

    # Parse the body HTML
    body_html = data.get("content", {}).get("body", "")
    soup = BeautifulSoup(body_html, "html.parser")

    # --- Title ---
    title_el = soup.find("h1")
    title = title_el.get_text(strip=True) if title_el else data.get("meta", {}).get("title", "Unknown Title")

    # --- Author ---
    author_el = soup.find("p", class_="author-name")
    if author_el:
        author_text = author_el.get_text(strip=True)
        author_text = re.sub(r"^By\s+", "", author_text)
        author = strip_author_titles(author_text)
    else:
        # Fallback to structuredData
        try:
            sd = json.loads(data.get("meta", {}).get("structuredData", "{}"))
            author = sd.get("mainEntity", {}).get("author", {}).get("name", "Unknown")
        except (json.JSONDecodeError, AttributeError):
            author = "Unknown"

    # --- Description (kicker) ---
    kicker_el = soup.find("p", class_="kicker")
    description = kicker_el.get_text(strip=True) if kicker_el else ""

    # --- Date ---
    # Try to get exact date from structuredData
    conf_date = None
    try:
        sd = json.loads(data.get("meta", {}).get("structuredData", "{}"))
        date_published = sd.get("datePublished", "")
        if date_published:
            conf_date = date.fromisoformat(date_published[:10])
    except (json.JSONDecodeError, ValueError):
        pass

    if not conf_date:
        # Fallback: first Saturday of the conference month
        conf_date = get_first_saturday(year, month_num)

    date_str = conf_date.strftime("%Y-%m-%d")

    # --- Body content ---
    body_block = soup.find("div", class_="body-block")
    content_md = html_to_markdown(body_block).strip() if body_block else ""
    # Collapse runs of 3+ newlines down to 2 (single blank line between paragraphs)
    content_md = re.sub(r"\n{3,}", "\n\n", content_md)

    # --- Footnotes ---
    footnotes_data = data.get("content", {}).get("footnotes", {})
    footnotes_md = format_footnotes(footnotes_data)

    # --- Session info ---
    session_name = fetch_session_info(year, month_num, uri, lang, headers)
    day_abbr, session_time = parse_session(session_name)

    # 3-letter month abbreviation
    month_abbr = calendar.month_abbr[month_num]

    return {
        "title": title,
        "date": date_str,
        "author": author,
        "description": description,
        "content": content_md,
        "footnotes": footnotes_md,
        "year": str(year),
        "month_abbr": month_abbr,
        "day_abbr": day_abbr,
        "session": session_time,
        "url": url,
    }


def build_markdown(fields):
    """Assemble the final markdown document from the extracted fields."""
    content = fields["content"]
    if fields["footnotes"]:
        content += "\n\n---\n\n## Notes\n\n" + fields["footnotes"]

    md = (
        f"---\n"
        f"Date: {fields['date']}\n"
        f"Author(s): \"[[{fields['author']}]]\"\n"
        f"Source: Church of Jesus Christ of Latter-day Saints\n"
        f"SourceURL: {fields['url']}\n"
        f"Description: {fields['description']}\n"
        f"tags: \n"
        f"   - GeneralConference\n"
        f"Category:\n"
        f"SubCategory:\n"
        f"---\n"
        f"\n"
        f"# {fields['title']}\n"
        f"\n"
        f"{content}\n"
    )
    return md


def sanitize_filename(name):
    """Remove characters that are invalid in filenames."""
    name = re.sub(r'[<>:"/\\|?*]', "", name)
    name = re.sub(r"\s+", " ", name).strip()
    return name


def main():
    url = input("Paste the General Conference talk URL: ").strip()

    if not url:
        print("No URL provided.")
        sys.exit(1)

    # Ensure lang parameter is present
    if "lang=" not in url:
        separator = "&" if "?" in url else "?"
        url += f"{separator}lang=eng"

    print("Scraping talk...")
    fields = scrape_talk(url)

    markdown = build_markdown(fields)

    safe_title = sanitize_filename(fields["title"])
    filename = (
        f"{fields['year']} {fields['month_abbr']}-"
        f"{fields['day_abbr']} {fields['session']}-"
        f"{safe_title}.md"
    )

    with open(filename, "w", encoding="utf-8") as f:
        f.write(markdown)

    print(f"\nTitle:  {fields['title']}")
    print(f"Author: {fields['author']}")
    print(f"Date:   {fields['date']}")
    print(f"Saved:  {filename}")


if __name__ == "__main__":
    main()
