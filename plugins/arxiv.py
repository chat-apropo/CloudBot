import feedparser
from dataclasses import dataclass
from cloudbot.util import formatting
from typing import Dict, List, Optional
import requests

from cloudbot import hook

API_URL = 'https://export.arxiv.org/api/query'
MAX_RESULTS = 3


class ApiError(Exception):
    pass


@dataclass
class SearchResult:
    title: str
    authors: List[str]
    summary: str
    link: str
    published: Optional[str] = None


@dataclass
class UserPage:
    query: str
    start: int
    sort_by_date: bool = False


def parse_arxiv_xml(xml_text: str) -> List[SearchResult]:
    feed = feedparser.parse(xml_text)
    results = []
    for entry in feed.entries:
        title = entry.title
        authors = [author.name for author in entry.authors]
        summary = entry.summary
        link = entry.link
        published = entry.published
        result = SearchResult(title=title, authors=authors,
                              summary=summary, link=link, published=published)
        results.append(result)
    return results


def search_arxiv(page: UserPage, max_results=10, sort_by_date: bool = False) -> List[SearchResult]:
    query = page.query
    start = page.start
    params = {
        'search_query': f'all:{query.casefold()}',
        'sortBy': 'relevance' if not sort_by_date else 'submittedDate',
        'start': start,
        'max_results': max_results,
        "SortOrder": "descending",
    }
    response = requests.get(API_URL, params=params)
    if response.status_code == 200:
        data = response.text
        return parse_arxiv_xml(data)
    raise ApiError(response.text)


def format_response(start: int, results: List[SearchResult]) -> List[str]:
    response = []
    for i, result in enumerate(results):
        parts = ""
        parts += formatting.truncate(f"\x02{start + i + 1})\x02 {result.title}", 120)
        if result.published:
            parts += f" {result.published}"
        parts += formatting.truncate(
            f" \x02Authors:\x02 {', '.join(result.authors)}.", 80)
        parts += formatting.truncate(f" {result.summary}", 240)
        parts = formatting.truncate(parts, 400)
        parts += f" :: {result.link}"
        response.append(parts)

    return response


user_pages: Dict[str, UserPage] = {}


@hook.command("arxiv", "ax")
def arxiv(text: str, nick: str):
    """<query> - Search arxiv.org for articles matching <query>. Can sort by date if query starts with '-t'."""
    global user_pages
    query = text.strip()
    if not query:
        return "Please provide a query"

    sort_by_date = False
    if query.startswith('-t'):
        sort_by_date = True
        query = query[2:].strip()

    user_pages[nick] = UserPage(query=query, start=0)
    results = search_arxiv(
        user_pages[nick], max_results=MAX_RESULTS, sort_by_date=sort_by_date)
    return format_response(0, results)


@hook.command("arxiv_next", "axn", autohelp=False)
def arxiv_next(text: str, nick: str):
    """Show next page of results"""
    global user_pages
    if text.strip():
        nick = text.strip()

    page = user_pages.get(nick)
    if not page:
        return f"No active search for {nick}"

    page.start += MAX_RESULTS
    results = search_arxiv(page, max_results=MAX_RESULTS,
                           sort_by_date=page.sort_by_date)
    return format_response(page.start, results)
