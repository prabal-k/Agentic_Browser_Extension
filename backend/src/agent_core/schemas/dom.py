"""DOM schemas — How the agent perceives web pages.

The agent never sees raw HTML. Instead, the content script (or Playwright)
extracts interactive elements and page metadata into these structured models.
This is the agent's "vision" of the page.

Design decisions:
- Each interactive element gets a numeric ID (assigned per extraction, not stable across pages)
- Elements include semantic information (type, role, text) so the LLM reasons about meaning, not CSS
- Bounding box is optional — used for visual grounding when available
- The agent sees a simplified, LLM-friendly representation, not the full DOM tree
"""

from enum import Enum
from pydantic import BaseModel, Field


class ElementType(str, Enum):
    """Semantic type of a DOM element.

    These types help the agent understand WHAT an element does
    without needing to parse HTML tag names or CSS classes.
    """

    BUTTON = "button"
    LINK = "link"
    TEXT_INPUT = "text_input"
    TEXTAREA = "textarea"
    SELECT = "select"
    CHECKBOX = "checkbox"
    RADIO = "radio"
    IMAGE = "image"
    HEADING = "heading"
    PARAGRAPH = "paragraph"
    LIST_ITEM = "list_item"
    NAV_ITEM = "nav_item"
    FORM = "form"
    DIALOG = "dialog"
    TAB = "tab"
    MENU_ITEM = "menu_item"
    ICON_BUTTON = "icon_button"
    FILE_INPUT = "file_input"
    SLIDER = "slider"
    TOGGLE = "toggle"
    OTHER = "other"


# Abbreviations for element types in LLM representation — saves ~5 chars per element
_TYPE_ABBREV = {
    "button": "btn", "link": "link", "text_input": "input",
    "textarea": "textarea", "select": "select", "checkbox": "chk",
    "radio": "radio", "image": "img", "heading": "h",
    "paragraph": "p", "list_item": "li", "nav_item": "nav",
    "form": "form", "dialog": "dialog", "tab": "tab",
    "menu_item": "menu", "icon_button": "icon-btn",
    "file_input": "file", "slider": "slider", "toggle": "toggle",
    "other": "el",
}


class BoundingBox(BaseModel):
    """Screen coordinates of an element. Optional — used for visual grounding."""

    x: float = Field(description="Left edge X coordinate in pixels")
    y: float = Field(description="Top edge Y coordinate in pixels")
    width: float = Field(description="Element width in pixels")
    height: float = Field(description="Element height in pixels")


class DOMElement(BaseModel):
    """A single interactive or notable element on the page.

    This is the atomic unit of what the agent can see and interact with.
    The agent refers to elements by their `element_id` when deciding actions.

    Example LLM representation:
        [14] button "Add to Cart" (visible, enabled)
        [15] text_input "Search products..." (visible, enabled, placeholder="Search")
    """

    element_id: int = Field(
        description="Numeric ID assigned during extraction. Unique within a single page snapshot."
    )
    fingerprint: str = Field(
        default="",
        description=(
            "Stable identity hash across re-extractions. When the numeric "
            "element_id is invalidated by a DOM mutation, the extension "
            "resolver falls back to this fingerprint. Derived from tag + "
            "role + visible text + parent context + bucketed bounding box."
        ),
    )
    element_type: ElementType = Field(
        description="Semantic type of the element (button, link, text_input, etc.)"
    )
    tag_name: str = Field(
        description="HTML tag name (button, a, input, div, etc.)"
    )
    text: str = Field(
        default="",
        description="Visible text content of the element, trimmed"
    )
    attributes: dict[str, str] = Field(
        default_factory=dict,
        description=(
            "Relevant HTML attributes: aria-label, placeholder, name, "
            "href, type, role, title, alt, value"
        ),
    )
    is_visible: bool = Field(
        default=True,
        description="Whether the element is currently visible in the viewport"
    )
    is_enabled: bool = Field(
        default=True,
        description="Whether the element is interactive (not disabled/readonly)"
    )
    is_focused: bool = Field(
        default=False,
        description="Whether the element currently has focus"
    )
    bounding_box: BoundingBox | None = Field(
        default=None,
        description="Screen coordinates, if available"
    )
    parent_context: str = Field(
        default="",
        description=(
            "Brief description of parent context. E.g., 'inside nav bar', "
            "'inside form: Login', 'inside modal: Confirm Order'. "
            "Helps the agent understand WHERE this element sits on the page."
        ),
    )
    children_count: int = Field(
        default=0,
        description="Number of child interactive elements (for containers like forms)"
    )
    css_selector: str = Field(
        default="",
        description="CSS selector that can locate this element. Fallback for element_id."
    )
    xpath: str = Field(
        default="",
        description="XPath that can locate this element. Secondary fallback."
    )
    is_leaf: bool = Field(
        default=True,
        description="Whether the element has no interactive children (leaf in the interaction tree)"
    )
    depth: int = Field(
        default=0,
        description="DOM depth relative to document.body"
    )

    def to_llm_representation(self) -> str:
        """Convert to a compact string the LLM can easily parse.

        Compressed format optimized for token efficiency:
            [14] btn "Add to Cart" [form:checkout]
            [15] input ph="Email" *focused [form:Login]
        """
        type_abbrev = _TYPE_ABBREV.get(self.element_type.value, self.element_type.value)
        parts = [f"[{self.element_id}]", type_abbrev]

        if self.text:
            # Truncate text aggressively — 50 chars is enough for identification
            display_text = self.text[:50] + ".." if len(self.text) > 50 else self.text
            # Clean whitespace
            display_text = " ".join(display_text.split())
            parts.append(f'"{display_text}"')

        # Only the MOST useful attributes — one or two max
        # Priority: placeholder > aria-label > href (truncated) > name
        attr_added = False
        if "placeholder" in self.attributes and self.attributes["placeholder"]:
            parts.append(f'ph="{self.attributes["placeholder"][:30]}"')
            attr_added = True
        elif "aria-label" in self.attributes and self.attributes["aria-label"]:
            parts.append(f'aria="{self.attributes["aria-label"][:30]}"')
            attr_added = True

        if not attr_added and "href" in self.attributes and self.attributes["href"]:
            href = self.attributes["href"]
            # Only show path, not full URL
            if href.startswith("http"):
                from urllib.parse import urlparse
                path = urlparse(href).path
                if path and path != "/":
                    parts.append(f'href="{path[:40]}"')
            elif len(href) <= 40:
                parts.append(f'href="{href}"')

        # State — show notable states (defaults: visible, enabled, not focused)
        if self.is_focused:
            parts.append("*focused")
        if not self.is_enabled:
            parts.append("*disabled")

        # Container tag — helps LLM distinguish wrappers from leaf elements
        if not self.is_leaf and self.children_count > 0:
            parts.append(f"[container:{self.children_count}]")

        # Parent context — compressed
        if self.parent_context:
            ctx = self.parent_context.replace("inside ", "")
            parts.append(f"[{ctx}]")

        return " ".join(parts)


class PageContext(BaseModel):
    """Complete snapshot of a web page as the agent sees it.

    This is sent to the LLM along with the user's goal so the agent
    can understand the current page state and decide what to do.

    Design: The agent sees the page as a structured document with
    metadata + a flat list of interactive elements, not a DOM tree.
    Trees are harder for LLMs to reason about than flat annotated lists.
    """

    url: str = Field(
        description="Current page URL"
    )
    title: str = Field(
        description="Page title from <title> tag"
    )
    meta_description: str = Field(
        default="",
        description="Meta description of the page, if available"
    )
    page_text_summary: str = Field(
        default="",
        description=(
            "Summarized visible text on the page (first ~500 chars). "
            "Gives the agent a quick understanding of page content."
        ),
    )
    elements: list[DOMElement] = Field(
        default_factory=list,
        description="All interactive/notable elements on the page"
    )
    forms: list[dict] = Field(
        default_factory=list,
        description=(
            "Detected form structures: [{name, action, method, field_ids: [int]}]. "
            "Helps the agent understand form groupings."
        ),
    )
    navigation: list[dict] = Field(
        default_factory=list,
        description=(
            "Detected navigation structures: [{label, element_ids: [int]}]. "
            "Helps the agent understand site navigation."
        ),
    )
    viewport_width: int = Field(
        default=1920,
        description="Browser viewport width in pixels"
    )
    viewport_height: int = Field(
        default=1080,
        description="Browser viewport height in pixels"
    )
    scroll_position: float = Field(
        default=0.0,
        description="Current scroll position as percentage (0.0 = top, 1.0 = bottom)"
    )
    has_more_content_below: bool = Field(
        default=False,
        description="Whether there is more content below the current viewport"
    )
    timestamp: float = Field(
        default=0.0,
        description="Unix timestamp when this snapshot was captured"
    )
    current_tab_id: int | None = Field(
        default=None,
        description="Chrome tab ID of the tab this snapshot was captured from"
    )
    open_tabs: list[dict] = Field(
        default_factory=list,
        description=(
            "All browser tabs currently open: [{tab_id, url, title, active}]. "
            "Enables the agent to reason about multi-tab workflows without "
            "having to call list_tabs explicitly."
        ),
    )

    @property
    def interactive_elements(self) -> list[DOMElement]:
        """Return only elements the agent can interact with."""
        interactive_types = {
            ElementType.BUTTON, ElementType.LINK, ElementType.TEXT_INPUT,
            ElementType.TEXTAREA, ElementType.SELECT, ElementType.CHECKBOX,
            ElementType.RADIO, ElementType.ICON_BUTTON, ElementType.TAB,
            ElementType.MENU_ITEM, ElementType.NAV_ITEM, ElementType.FILE_INPUT,
            ElementType.SLIDER, ElementType.TOGGLE,
        }
        return [
            el for el in self.elements
            if el.element_type in interactive_types and el.is_enabled
        ]

    def to_llm_representation(self, compact: bool = False, max_elements: int | None = None) -> str:
        """Convert entire page context to LLM-friendly text.

        Args:
            compact: If True, use minimal representation (for evaluate node).
                     If False, full representation (for decide_action node).
            max_elements: Optional cap on interactive elements rendered. When
                     set and the page has more, output truncates and appends
                     a "(showing top N of M)" note so the LLM knows there is
                     more to scroll / search for.
        """
        lines = [
            f"URL: {self.url}",
            f"Title: {self.title}",
        ]

        # Multi-tab awareness: only surface when there are 2+ tabs (single-tab
        # is the norm and would just waste prompt budget).
        if self.open_tabs and len(self.open_tabs) >= 2:
            tab_lines = []
            for t in self.open_tabs[:8]:  # cap to avoid prompt bloat
                tid = t.get("tab_id", "?")
                url = (t.get("url", "") or "")[:60]
                title = (t.get("title", "") or "")[:40]
                marker = "*" if t.get("active") or tid == self.current_tab_id else " "
                tab_lines.append(f"  {marker}[{tid}] {title} — {url}")
            lines.append(f"Tabs ({len(self.open_tabs)} open, current={self.current_tab_id}):")
            lines.extend(tab_lines)

        if self.has_more_content_below:
            lines.append(f"Scroll: {self.scroll_position:.0%} (more below)")

        # Page summary — truncated for action prompts, longer for evaluation
        if self.page_text_summary:
            max_summary = 500 if not compact else 300
            summary = self.page_text_summary[:max_summary]
            if len(self.page_text_summary) > max_summary:
                summary += ".."
            lines.append(f"Text: {summary}")

        interactive = self.interactive_elements
        total_interactive = len(interactive)

        # Apply element cap. In compact mode, default cap is 30 for backwards
        # compat. In full mode, honor caller-provided max_elements (decision
        # nodes pass 40); None means no cap (extraction callers).
        if compact:
            cap = max_elements if max_elements is not None else 30
        else:
            cap = max_elements

        shown = interactive if cap is None else interactive[:cap]
        truncated = cap is not None and total_interactive > cap

        header = f"\nElements ({total_interactive}"
        if truncated:
            header += f", showing top {cap}"
        header += "):"
        lines.append(header)

        if compact:
            for el in shown:
                abbrev = _TYPE_ABBREV.get(el.element_type.value, el.element_type.value)
                text = el.text[:25] + ".." if el.text and len(el.text) > 25 else (el.text or "")
                line = f"  [{el.element_id}] {abbrev}"
                if text:
                    line += f' "{text}"'
                lines.append(line)
        else:
            for element in shown:
                lines.append(f"  {element.to_llm_representation()}")

        if truncated:
            lines.append(f"  ... ({total_interactive - cap} more elements not shown — scroll or refine target)")

        if not interactive:
            lines.append("  (no interactive elements)")

        # Key page content — headings only (most informative, least tokens)
        headings = [
            el for el in self.elements
            if el.element_type == ElementType.HEADING and el.text
        ]
        if headings:
            lines.append("\nHeadings:")
            for el in headings[:6]:
                text = el.text[:60] + ".." if len(el.text) > 60 else el.text
                text = " ".join(text.split())
                lines.append(f"  {text}")

        return "\n".join(lines)
