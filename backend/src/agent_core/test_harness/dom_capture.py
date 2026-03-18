"""DOM Snapshot Capture — Generate PageContext JSON from real websites.

Uses requests + BeautifulSoup to fetch pages and extract interactive elements
into the same format the extension will produce.

Limitations vs real extension:
- Cannot execute JavaScript (no dynamic content)
- Cannot detect visibility or bounding boxes
- Cannot interact with SPAs (client-side rendering)
- Good enough for testing agent reasoning on static-ish pages

Usage:
    python -m agent_core.test_harness.dom_capture https://example.com --output example.json
"""

import json
import time
from pathlib import Path

import click
import httpx
from bs4 import BeautifulSoup, Tag
from rich.console import Console

from agent_core.schemas.dom import DOMElement, PageContext, ElementType

console = Console()


# Map HTML tags and attributes to ElementType
_TAG_TYPE_MAP: dict[str, ElementType] = {
    "button": ElementType.BUTTON,
    "a": ElementType.LINK,
    "textarea": ElementType.TEXTAREA,
    "select": ElementType.SELECT,
    "h1": ElementType.HEADING,
    "h2": ElementType.HEADING,
    "h3": ElementType.HEADING,
    "h4": ElementType.HEADING,
    "h5": ElementType.HEADING,
    "h6": ElementType.HEADING,
    "p": ElementType.PARAGRAPH,
    "li": ElementType.LIST_ITEM,
    "nav": ElementType.NAV_ITEM,
    "img": ElementType.IMAGE,
}

_INPUT_TYPE_MAP: dict[str, ElementType] = {
    "text": ElementType.TEXT_INPUT,
    "email": ElementType.TEXT_INPUT,
    "password": ElementType.TEXT_INPUT,
    "search": ElementType.TEXT_INPUT,
    "tel": ElementType.TEXT_INPUT,
    "url": ElementType.TEXT_INPUT,
    "number": ElementType.TEXT_INPUT,
    "checkbox": ElementType.CHECKBOX,
    "radio": ElementType.RADIO,
    "submit": ElementType.BUTTON,
    "button": ElementType.BUTTON,
    "file": ElementType.FILE_INPUT,
    "range": ElementType.SLIDER,
}


def _get_element_type(tag: Tag) -> ElementType:
    """Determine the semantic ElementType from an HTML tag."""
    tag_name = tag.name.lower()

    if tag_name == "input":
        input_type = (tag.get("type") or "text").lower()
        return _INPUT_TYPE_MAP.get(input_type, ElementType.TEXT_INPUT)

    if tag_name in _TAG_TYPE_MAP:
        return _TAG_TYPE_MAP[tag_name]

    # Check role attribute
    role = (tag.get("role") or "").lower()
    role_map = {
        "button": ElementType.BUTTON,
        "link": ElementType.LINK,
        "textbox": ElementType.TEXT_INPUT,
        "checkbox": ElementType.CHECKBOX,
        "radio": ElementType.RADIO,
        "tab": ElementType.TAB,
        "menuitem": ElementType.MENU_ITEM,
        "navigation": ElementType.NAV_ITEM,
        "dialog": ElementType.DIALOG,
        "switch": ElementType.TOGGLE,
        "slider": ElementType.SLIDER,
    }
    if role in role_map:
        return role_map[role]

    return ElementType.OTHER


def _get_text(tag: Tag) -> str:
    """Extract visible text from a tag, cleaned up."""
    text = tag.get_text(strip=True)
    # Collapse whitespace
    text = " ".join(text.split())
    return text[:200]  # Limit length


def _get_relevant_attributes(tag: Tag) -> dict[str, str]:
    """Extract attributes relevant for LLM understanding."""
    relevant = {}
    for attr_name in ("aria-label", "placeholder", "name", "href", "type",
                      "role", "title", "alt", "value", "action", "method"):
        val = tag.get(attr_name)
        if val:
            if isinstance(val, list):
                val = " ".join(val)
            relevant[attr_name] = str(val)[:200]
    return relevant


def _find_parent_context(tag: Tag) -> str:
    """Determine the parent context of an element."""
    for parent in tag.parents:
        if not isinstance(parent, Tag):
            continue

        if parent.name == "nav":
            label = parent.get("aria-label") or "navigation"
            return f"inside nav: {label}"
        if parent.name == "form":
            name = parent.get("name") or parent.get("id") or parent.get("aria-label") or "form"
            return f"inside form: {name}"
        if parent.name == "header":
            return "inside header"
        if parent.name == "footer":
            return "inside footer"
        if parent.name == "main":
            return "main content"
        if parent.name in ("dialog", "modal"):
            return f"inside dialog"
        if parent.get("role") == "dialog":
            return f"inside dialog: {parent.get('aria-label', '')}"

    return ""


def _build_css_selector(tag: Tag) -> str:
    """Build a reasonable CSS selector for an element."""
    parts = [tag.name]
    if tag.get("id"):
        return f"{tag.name}#{tag['id']}"
    if tag.get("name"):
        parts.append(f"[name='{tag['name']}']")
    elif tag.get("class"):
        classes = tag["class"] if isinstance(tag["class"], list) else [tag["class"]]
        parts.append(f".{'.'.join(classes[:2])}")
    return "".join(parts)


def capture_dom(url: str, timeout: int = 10) -> PageContext:
    """Capture a DOM snapshot from a URL.

    Args:
        url: The webpage URL to capture.
        timeout: Request timeout in seconds.

    Returns:
        PageContext with extracted elements.
    """
    console.print(f"[dim]Fetching {url}...[/dim]")

    response = httpx.get(
        url,
        timeout=timeout,
        follow_redirects=True,
        headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0"},
    )
    response.raise_for_status()

    soup = BeautifulSoup(response.text, "html.parser")

    # Extract page metadata
    title_tag = soup.find("title")
    title = title_tag.get_text(strip=True) if title_tag else ""

    meta_desc = soup.find("meta", attrs={"name": "description"})
    meta_description = meta_desc["content"] if meta_desc and meta_desc.get("content") else ""

    # Extract visible text summary
    body = soup.find("body")
    body_text = body.get_text(" ", strip=True) if body else ""
    page_text_summary = " ".join(body_text.split())[:500]

    # Extract interactive elements
    interactive_tags = [
        "a", "button", "input", "textarea", "select",
        "h1", "h2", "h3", "h4", "h5", "h6", "p", "img",
    ]
    # Also find elements with role attributes
    role_elements = soup.find_all(attrs={"role": True})

    elements: list[DOMElement] = []
    seen_selectors: set[str] = set()
    element_id = 1

    for tag_name in interactive_tags:
        for tag in soup.find_all(tag_name):
            selector = _build_css_selector(tag)
            if selector in seen_selectors:
                continue
            seen_selectors.add(selector)

            text = _get_text(tag)
            el_type = _get_element_type(tag)

            # Skip empty non-interactive elements
            if el_type in {ElementType.PARAGRAPH, ElementType.HEADING} and not text:
                continue
            # Skip very short paragraphs
            if el_type == ElementType.PARAGRAPH and len(text) < 10:
                continue
            # Limit paragraphs to keep snapshot manageable
            if el_type == ElementType.PARAGRAPH and len([e for e in elements if e.element_type == ElementType.PARAGRAPH]) > 5:
                continue

            elements.append(DOMElement(
                element_id=element_id,
                element_type=el_type,
                tag_name=tag.name,
                text=text,
                attributes=_get_relevant_attributes(tag),
                is_visible=True,  # Can't detect without JS
                is_enabled=not tag.has_attr("disabled"),
                parent_context=_find_parent_context(tag),
                css_selector=selector,
            ))
            element_id += 1

    # Add role-based elements not already captured
    for tag in role_elements:
        if tag.name in interactive_tags:
            continue
        selector = _build_css_selector(tag)
        if selector in seen_selectors:
            continue
        seen_selectors.add(selector)

        elements.append(DOMElement(
            element_id=element_id,
            element_type=_get_element_type(tag),
            tag_name=tag.name,
            text=_get_text(tag),
            attributes=_get_relevant_attributes(tag),
            is_visible=True,
            is_enabled=True,
            parent_context=_find_parent_context(tag),
            css_selector=selector,
        ))
        element_id += 1

    # Extract forms
    forms = []
    for form_tag in soup.find_all("form"):
        form_name = form_tag.get("name") or form_tag.get("id") or form_tag.get("aria-label") or "unnamed"
        field_ids = [
            el.element_id for el in elements
            if el.parent_context and form_name.lower() in el.parent_context.lower()
        ]
        forms.append({
            "name": form_name,
            "action": form_tag.get("action", ""),
            "method": (form_tag.get("method") or "GET").upper(),
            "field_ids": field_ids,
        })

    # Extract navigation
    navigations = []
    for nav_tag in soup.find_all("nav"):
        nav_label = nav_tag.get("aria-label") or "navigation"
        nav_ids = [
            el.element_id for el in elements
            if el.parent_context and "nav" in el.parent_context.lower()
        ]
        if nav_ids:
            navigations.append({"label": nav_label, "element_ids": nav_ids})

    return PageContext(
        url=str(response.url),
        title=title,
        meta_description=meta_description,
        page_text_summary=page_text_summary,
        elements=elements,
        forms=forms,
        navigation=navigations,
        viewport_width=1920,
        viewport_height=1080,
        scroll_position=0.0,
        has_more_content_below=len(elements) > 20,
        timestamp=time.time(),
    )


@click.command()
@click.argument("url")
@click.option("--output", "-o", default=None, help="Output file path (default: auto-named)")
def capture_cli(url: str, output: str | None):
    """Capture a DOM snapshot from a URL and save as JSON."""
    try:
        page_ctx = capture_dom(url)
    except Exception as e:
        console.print(f"[red]Error capturing {url}: {e}[/red]")
        return

    console.print(f"[green]Captured {len(page_ctx.elements)} elements from {page_ctx.title}[/green]")

    if output is None:
        # Auto-generate filename from URL
        from urllib.parse import urlparse
        parsed = urlparse(url)
        name = parsed.netloc.replace(".", "_").replace("www_", "")
        if parsed.path and parsed.path != "/":
            name += parsed.path.replace("/", "_").rstrip("_")
        output = f"{name}.json"

    output_path = Path(output)
    with open(output_path, "w") as f:
        json.dump(page_ctx.model_dump(), f, indent=2, default=str)

    console.print(f"[green]Saved to {output_path}[/green]")
    console.print(f"  Elements: {len(page_ctx.elements)}")
    console.print(f"  Interactive: {len(page_ctx.interactive_elements)}")
    console.print(f"  Forms: {len(page_ctx.forms)}")


if __name__ == "__main__":
    capture_cli()
