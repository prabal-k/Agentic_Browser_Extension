"""Browser tool definitions for the cognitive agent.

These are LangGraph-compatible tool definitions that describe browser actions.
The agent's LLM uses these to decide which action to take.

IMPORTANT: These tools do NOT execute browser actions directly.
They produce Action objects that are sent to the browser (extension or Playwright)
for execution. The result comes back asynchronously.

Design decisions:
- Tools are intentionally simple and composable
- Each tool maps to exactly one ActionType
- Descriptions are written for the LLM — they explain WHEN and WHY to use each tool
- Risk levels guide the confirmation logic
"""

from langchain_core.tools import tool

from agent_core.schemas.actions import Action, ActionType


# ============================================================
# Element Interactions
# ============================================================

@tool
def click(element_id: int, description: str = "") -> Action:
    """Click on an element on the page.

    USE WHEN: You need to press a button, follow a link, select a tab,
    toggle a checkbox, or interact with any clickable element.

    IMPORTANT:
    - Always verify the element_id exists in the current page context
    - Check that the element is visible and enabled before clicking
    - For links that navigate away, be aware the page will change

    Args:
        element_id: The numeric ID of the element to click (from page context)
        description: Why you are clicking this element
    """
    return Action(
        action_type=ActionType.CLICK,
        element_id=element_id,
        description=description,
        risk_level="low",
    )


@tool
def type_text(element_id: int, text: str, submit: bool = False, description: str = "") -> Action:
    """Type text into an input field. Clears existing content first.

    USE WHEN: You need to type into any input — search bars, login forms,
    text fields, address inputs, etc.

    Set submit=True to press Enter after typing (for search bars and forms).

    Args:
        element_id: The numeric ID of the input element
        text: The text to type
        submit: If True, press Enter after typing
        description: Why you are typing this text
    """
    value = f"{text}|SUBMIT" if submit else text
    return Action(
        action_type=ActionType.CLEAR_AND_TYPE,
        element_id=element_id,
        value=value,
        description=description,
        risk_level="low",
    )


@tool
def select_option(element_id: int, value: str, description: str = "") -> Action:
    """Select an option from a dropdown/select element.

    USE WHEN: You need to choose from a dropdown menu or select element.

    Args:
        element_id: The numeric ID of the select element
        value: The option value or visible text to select
        description: Why you are selecting this option
    """
    return Action(
        action_type=ActionType.SELECT_OPTION,
        element_id=element_id,
        value=value,
        description=description,
        risk_level="low",
    )


@tool
def hover(element_id: int, description: str = "") -> Action:
    """Hover over an element to trigger tooltips, dropdowns, or hover effects.

    USE WHEN: You need to reveal hidden menus, tooltips, or hover-triggered content.

    Args:
        element_id: The element to hover over
        description: What you expect to appear on hover
    """
    return Action(
        action_type=ActionType.HOVER,
        element_id=element_id,
        description=description,
        risk_level="low",
        requires_confirmation=False,
    )


@tool
def check(element_id: int, description: str = "") -> Action:
    """Check a checkbox or toggle a switch ON.

    USE WHEN: You need to enable an option, agree to terms, or select a checkbox.

    Args:
        element_id: The checkbox or toggle element
        description: What option you are enabling
    """
    return Action(
        action_type=ActionType.CHECK,
        element_id=element_id,
        description=description,
        risk_level="low",
    )


@tool
def uncheck(element_id: int, description: str = "") -> Action:
    """Uncheck a checkbox or toggle a switch OFF.

    USE WHEN: You need to disable an option or deselect a checkbox.

    Args:
        element_id: The checkbox or toggle element
        description: What option you are disabling
    """
    return Action(
        action_type=ActionType.UNCHECK,
        element_id=element_id,
        description=description,
        risk_level="low",
    )


# ============================================================
# Navigation
# ============================================================

@tool
def navigate(url: str, description: str = "") -> Action:
    """Navigate to a specific URL.

    USE WHEN: You need to go to a specific website or page.
    The current page will be replaced.

    IMPORTANT:
    - Use full URLs with https://
    - This will cause a full page load — previous page context will be replaced

    Args:
        url: The full URL to navigate to (e.g., https://youtube.com)
        description: Why you are navigating to this URL
    """
    return Action(
        action_type=ActionType.NAVIGATE,
        value=url,
        description=description,
        risk_level="low",
    )


@tool
def go_back(description: str = "") -> Action:
    """Go back to the previous page (browser back button).

    USE WHEN: You navigated to the wrong page or need to return.

    Args:
        description: Why you are going back
    """
    return Action(
        action_type=ActionType.GO_BACK,
        description=description,
        risk_level="low",
    )


@tool
def go_forward(description: str = "") -> Action:
    """Go forward to the next page (browser forward button).

    USE WHEN: You went back but need to go forward again.

    Args:
        description: Why you are going forward
    """
    return Action(
        action_type=ActionType.GO_FORWARD,
        description=description,
        risk_level="low",
    )


@tool
def refresh(description: str = "") -> Action:
    """Reload the current page.

    USE WHEN: The page seems stale, content didn't load correctly,
    or you need a fresh page state.

    Args:
        description: Why you are refreshing
    """
    return Action(
        action_type=ActionType.REFRESH,
        description=description,
        risk_level="low",
    )


# ============================================================
# Scrolling
# ============================================================

@tool
def scroll_down(amount: int = 3, description: str = "") -> Action:
    """Scroll down the page to see more content.

    USE WHEN: The element you need is not visible, or you need to
    discover more content below the current viewport.

    Args:
        amount: Number of viewport heights to scroll (1 = one full screen)
        description: Why you are scrolling
    """
    return Action(
        action_type=ActionType.SCROLL_DOWN,
        value=str(amount),
        description=description,
        risk_level="low",
    )


@tool
def scroll_up(amount: int = 3, description: str = "") -> Action:
    """Scroll up the page.

    USE WHEN: You need to go back to content above the current viewport.

    Args:
        amount: Number of viewport heights to scroll up
        description: Why you are scrolling up
    """
    return Action(
        action_type=ActionType.SCROLL_UP,
        value=str(amount),
        description=description,
        risk_level="low",
    )


@tool
def scroll_to_element(element_id: int, description: str = "") -> Action:
    """Scroll until a specific element is visible.

    USE WHEN: You know an element exists but it's outside the viewport.

    Args:
        element_id: The element to scroll to
        description: Why you need this element visible
    """
    return Action(
        action_type=ActionType.SCROLL_TO_ELEMENT,
        element_id=element_id,
        description=description,
        risk_level="low",
    )


# ============================================================
# Keyboard
# ============================================================

@tool
def press_key(key: str, description: str = "") -> Action:
    """Press a keyboard key.

    USE WHEN: You need to press Enter to submit a form, Escape to close
    a modal, Tab to move focus, or any other keyboard key.

    Args:
        key: Key name (Enter, Escape, Tab, ArrowDown, ArrowUp, Space, Backspace)
        description: Why you are pressing this key
    """
    return Action(
        action_type=ActionType.PRESS_KEY,
        value=key,
        description=description,
        risk_level="low",
    )


@tool
def key_combo(keys: str, description: str = "") -> Action:
    """Press a keyboard shortcut (combination of keys).

    USE WHEN: You need to use Ctrl+A (select all), Ctrl+C (copy),
    Ctrl+V (paste), Ctrl+Enter (submit), or other keyboard shortcuts.

    Args:
        keys: Key combination (e.g., "Ctrl+A", "Ctrl+Shift+Enter", "Alt+F4")
        description: Why you are pressing this key combination
    """
    return Action(
        action_type=ActionType.KEY_COMBO,
        value=keys,
        description=description,
        risk_level="low",
    )


# ============================================================
# Tab Management
# ============================================================

@tool
def new_tab(url: str = "", description: str = "") -> Action:
    """Open a new browser tab, optionally navigating to a URL.

    USE WHEN: You need to open a link in a new tab, or open a new page
    while keeping the current one open.

    Args:
        url: URL to open in the new tab (empty for blank tab)
        description: Why you are opening a new tab
    """
    return Action(
        action_type=ActionType.NEW_TAB,
        value=url,
        description=description,
        risk_level="low",
    )


@tool
def close_tab(description: str = "") -> Action:
    """Close the current browser tab.

    USE WHEN: You are done with the current tab and want to return
    to the previous one. Be careful not to close the last tab.

    Args:
        description: Why you are closing this tab
    """
    return Action(
        action_type=ActionType.CLOSE_TAB,
        description=description,
        risk_level="medium",
    )


@tool
def switch_tab(tab_index: int, description: str = "") -> Action:
    """Switch to a different browser tab by index.

    USE WHEN: You have multiple tabs open and need to switch between them.
    Tab indices start at 0.

    Args:
        tab_index: The index of the tab to switch to (0-based)
        description: Why you are switching tabs
    """
    return Action(
        action_type=ActionType.SWITCH_TAB,
        value=str(tab_index),
        description=description,
        risk_level="low",
    )


# ============================================================
# Information Gathering (read-only, no side effects)
# ============================================================

@tool
def extract_text(element_id: int, description: str = "") -> Action:
    """Extract text content from a specific element.

    USE WHEN: You need to read content from the page that isn't
    captured in the page summary. This does NOT modify the page.

    Args:
        element_id: The element to extract text from
        description: What information you expect to find
    """
    return Action(
        action_type=ActionType.EXTRACT_TEXT,
        element_id=element_id,
        description=description,
        risk_level="low",
        is_reversible=True,
        requires_confirmation=False,
    )


@tool
def extract_table(element_id: int, description: str = "") -> Action:
    """Extract structured data from a table element.

    USE WHEN: You need to read data from an HTML table, such as
    a pricing table, comparison chart, or data grid.

    Args:
        element_id: The table element to extract data from
        description: What data you expect to find in the table
    """
    return Action(
        action_type=ActionType.EXTRACT_TABLE,
        element_id=element_id,
        description=description,
        risk_level="low",
        is_reversible=True,
        requires_confirmation=False,
    )


@tool
def extract_listings(description: str = "") -> Action:
    """Extract structured data from product listings, search results, or any repeated card/grid layout.

    USE WHEN: The page has a grid or list of items (products, search results,
    articles, job postings, etc.) and you need structured data like:
    name, price, image URL, product URL, rating, description.

    Works on ANY website — auto-detects repeated card structures in the DOM.
    Returns JSON array of items with all available fields.

    Use this INSTEAD OF read_page when you need structured data from listings.

    Args:
        description: What kind of items to extract (e.g. "Lenovo laptops with prices")
    """
    return Action(
        action_type=ActionType.EXTRACT_TEXT,
        value="__EXTRACT_LISTINGS__",
        description=description or "Extracting structured listing data",
        risk_level="low",
        is_reversible=True,
        requires_confirmation=False,
    )


@tool
def take_screenshot(description: str = "") -> Action:
    """Take a screenshot of the current page.

    USE WHEN: You need to visually inspect the page, or the page
    uses canvas/images that cannot be extracted as text.

    Args:
        description: What you want to see in the screenshot
    """
    return Action(
        action_type=ActionType.TAKE_SCREENSHOT,
        description=description,
        risk_level="low",
        is_reversible=True,
        requires_confirmation=False,
    )


# ============================================================
# Monitoring (read-only)
# ============================================================

@tool
def get_console_logs(description: str = "") -> Action:
    """Get recent browser console messages (log, warn, error).

    USE WHEN: A page action seems to silently fail, you want to check
    for JavaScript errors, or you need to debug page behavior.

    Args:
        description: What you're looking for in the console
    """
    return Action(
        action_type=ActionType.GET_CONSOLE_LOGS,
        description=description,
        risk_level="low",
        is_reversible=True,
        requires_confirmation=False,
    )


@tool
def get_network_log(description: str = "") -> Action:
    """Get recent network requests (XHR/fetch calls made by the page).

    USE WHEN: You want to check if an action triggered an API call,
    see if a form submission succeeded, or detect loading states.

    Args:
        description: What network activity you're looking for
    """
    return Action(
        action_type=ActionType.GET_NETWORK_LOG,
        description=description,
        risk_level="low",
        is_reversible=True,
        requires_confirmation=False,
    )


# ============================================================
# JavaScript Execution
# ============================================================

@tool
def evaluate_js(code: str, description: str = "") -> Action:
    """Execute JavaScript code in the page context and return the result.

    USE WHEN: You need to extract data that isn't in the DOM snapshot,
    interact with page JavaScript APIs, or work around inaccessible elements.

    IMPORTANT: Only use for reading data or simple interactions.
    Do not use for destructive operations.

    Args:
        code: JavaScript code to execute (e.g., "document.title" or "document.querySelectorAll('.price').length")
        description: What you expect the code to return
    """
    return Action(
        action_type=ActionType.EVALUATE_JS,
        value=code,
        description=description,
        risk_level="medium",
        requires_confirmation=True,
    )


# ============================================================
# Dialog Handling
# ============================================================

@tool
def handle_dialog(action_value: str = "accept", description: str = "") -> Action:
    """Handle a browser dialog (alert, confirm, prompt).

    USE WHEN: A dialog/popup has appeared that blocks page interaction.
    Common dialogs: cookie consent, age verification, alert boxes.

    Args:
        action_value: "accept" to click OK/Yes, "dismiss" to click Cancel/No,
                     or text to enter in a prompt dialog before accepting
        description: What dialog you are handling
    """
    return Action(
        action_type=ActionType.HANDLE_DIALOG,
        value=action_value,
        description=description,
        risk_level="low",
    )


# ============================================================
# File & Drag
# ============================================================

@tool
def upload_file(element_id: int, file_path: str, description: str = "") -> Action:
    """Upload a file to a file input element.

    USE WHEN: You need to attach a file to a form (e.g., profile picture,
    document upload, CSV import).

    Args:
        element_id: The file input element ID
        file_path: Path to the file to upload
        description: What file you are uploading and why
    """
    return Action(
        action_type=ActionType.UPLOAD_FILE,
        element_id=element_id,
        value=file_path,
        description=description,
        risk_level="medium",
    )


@tool
def drag(source_element_id: int, target_element_id: int, description: str = "") -> Action:
    """Drag an element and drop it onto another element.

    USE WHEN: You need to reorder items (Kanban boards, playlists),
    move files, or interact with drag-based UIs (sliders, color pickers).

    Args:
        source_element_id: The element to drag
        target_element_id: The element to drop onto
        description: What you are dragging and where
    """
    return Action(
        action_type=ActionType.DRAG,
        element_id=source_element_id,
        value=str(target_element_id),
        description=description,
        risk_level="low",
    )


# ============================================================
# Smart Waiting
# ============================================================

@tool
def wait_for_selector(css_selector: str, timeout_seconds: float = 10.0, description: str = "") -> Action:
    """Wait until a specific CSS selector appears in the DOM.

    USE WHEN: You expect dynamic content to load (e.g., search results,
    modal dialogs, lazy-loaded sections) and need to wait for it.

    Args:
        css_selector: CSS selector to wait for (e.g., ".search-results", "#modal")
        timeout_seconds: Max seconds to wait (default 10)
        description: What element you're waiting for
    """
    return Action(
        action_type=ActionType.WAIT_FOR_SELECTOR,
        value=f"{css_selector}|{timeout_seconds}",
        description=description,
        risk_level="low",
        requires_confirmation=False,
    )


@tool
def wait_for_navigation(timeout_seconds: float = 10.0, description: str = "") -> Action:
    """Wait until the page URL changes (navigation occurs).

    USE WHEN: You clicked a link or submitted a form and need to
    wait for the page to navigate before continuing.

    Args:
        timeout_seconds: Max seconds to wait (default 10)
        description: What navigation you're waiting for
    """
    return Action(
        action_type=ActionType.WAIT_FOR_NAVIGATION,
        value=str(timeout_seconds),
        description=description,
        risk_level="low",
        requires_confirmation=False,
    )


# ============================================================
# Special
# ============================================================

@tool
def wait(seconds: float = 2.0, description: str = "") -> Action:
    """Wait for a specified duration.

    USE WHEN: You need to wait for a page to load, an animation to complete,
    or dynamic content to appear.

    Args:
        seconds: How long to wait (max 10 seconds)
        description: What you're waiting for
    """
    clamped = min(max(seconds, 0.5), 10.0)
    return Action(
        action_type=ActionType.WAIT,
        value=str(clamped),
        description=description,
        risk_level="low",
        requires_confirmation=False,
    )


@tool
def visual_check(description: str) -> Action:
    """Take a screenshot and visually analyze the current page using a vision AI model.

    USE WHEN: You need to see what's visually on the page — photos, images, charts,
    product displays, maps, or any content that can't be read as text.

    IMPORTANT: The description you provide becomes the QUESTION asked to the vision model.
    Be specific about what you're looking for. Examples:
    - "Are there any vape or e-cigarette products visible in this photo?"
    - "What is the price shown for the iPhone 15 Pro Max?"
    - "Does this store's photo show tobacco or smoking products?"
    - "What products are displayed on the shelves in this image?"

    Do NOT use generic descriptions like "analyze the page". Be specific about the task.

    Args:
        description: The specific question to ask about what's visible on screen
    """
    return Action(
        action_type=ActionType.TAKE_SCREENSHOT,
        value=f"__VISUAL_CHECK__|{description}",
        description=description or "Visual analysis of current page",
        risk_level="low",
        is_reversible=True,
        requires_confirmation=False,
    )


@tool
def read_page(description: str = "") -> Action:
    """Read the visible text content of the current page.

    USE WHEN: You need to read information from the page that isn't in
    the element list — such as prices, product details, article text,
    search result snippets, error messages, or any other visible content.

    This returns the full visible text of the current viewport, not just
    interactive elements. Use this BEFORE declaring "done" when the goal
    involves finding/reading information.

    Args:
        description: What information you're looking for on the page
    """
    return Action(
        action_type=ActionType.EXTRACT_TEXT,
        value="__READ_PAGE__",
        description=description or "Reading visible page content",
        risk_level="low",
        is_reversible=True,
        requires_confirmation=False,
    )


@tool
def fill_form(fields: str, submit: bool = True, description: str = "") -> Action:
    """Fill multiple form fields at once and optionally submit.

    USE WHEN: You see a form with multiple fields (login form, search + filters,
    registration, checkout). Instead of typing into each field separately
    (which requires a separate LLM call each), fill ALL fields in one action.

    The fields parameter is a JSON string mapping element_id to value:
    - fields='{"5": "user@email.com", "6": "mypassword123"}'
    - fields='{"10": "1411 Howland Blvd, Deltona, FL", "12": "15"}'

    Args:
        fields: JSON string mapping element_id (as string) to the value to type.
                Example: '{"5": "user@email.com", "6": "password123"}'
        submit: If True, press Enter after filling all fields to submit the form.
        description: What form you are filling and why.
    """
    return Action(
        action_type=ActionType.CLEAR_AND_TYPE,
        value=f"__FILL_FORM__|{fields}|{'SUBMIT' if submit else 'NO_SUBMIT'}",
        description=description or "Fill form fields",
        risk_level="medium",
        requires_confirmation=False,
    )


@tool
def ask_user(question: str, context: str = "") -> Action:
    """Ask the user a question when you need clarification.

    USE WHEN:
    - You are unsure which option the user wants
    - You need information not available on the page (e.g., address, password)
    - Multiple valid interpretations of the goal exist
    - You need confirmation before a high-risk action

    DO NOT USE WHEN:
    - The answer is clearly available on the page
    - You can make a reasonable assumption

    Args:
        question: The specific question to ask the user
        context: Additional context about why you're asking
    """
    return Action(
        action_type=ActionType.DONE,  # Will be intercepted by the interrupt node
        value=question,
        description=f"Asking user: {question}",
        risk_level="low",
        requires_confirmation=False,
    )


@tool
def done(summary: str) -> Action:
    """Mark the task as complete.

    USE WHEN: The goal has been fully achieved, or you've determined
    the goal cannot be achieved and have informed the user.

    Args:
        summary: Summary of what was accomplished
    """
    return Action(
        action_type=ActionType.DONE,
        value=summary,
        description=summary,
        risk_level="low",
        requires_confirmation=False,
    )


# Collect all tools for registration with the LLM
BROWSER_TOOLS = [
    # Element interactions
    click,
    type_text,
    select_option,
    hover,
    check,
    uncheck,
    # Navigation
    navigate,
    go_back,
    go_forward,
    refresh,
    # Scrolling
    scroll_down,
    scroll_up,
    scroll_to_element,
    # Keyboard
    press_key,
    key_combo,
    # Tab management
    new_tab,
    close_tab,
    switch_tab,
    # Information gathering
    extract_text,
    extract_table,
    extract_listings,
    read_page,
    visual_check,
    take_screenshot,
    # Monitoring (stubs — not in default groups)
    get_console_logs,
    get_network_log,
    # JavaScript
    evaluate_js,
    # Dialogs
    handle_dialog,
    # File & drag
    upload_file,
    drag,
    # Smart waiting
    wait_for_selector,
    wait_for_navigation,
    # Special
    wait,
    ask_user,
    done,
]


# Tool groups for dynamic selection — core is always included
# Design: core has everything needed for 80% of tasks (11 tools)
TOOL_GROUPS = {
    "core": [click, type_text, navigate, go_back, scroll_down,
             press_key, read_page, visual_check, extract_text, wait, ask_user, done],
    "search": [select_option, scroll_up, scroll_to_element, key_combo],
    "tabs": [new_tab, close_tab, switch_tab],
    "forms": [check, uncheck, hover, upload_file],
    "data": [extract_table, extract_listings, evaluate_js],
    "advanced": [drag, handle_dialog, go_forward, refresh],
    "waiting": [wait_for_selector, wait_for_navigation],
    # monitoring removed from groups — stubs that return empty data
}


def get_tool_descriptions() -> str:
    """Get formatted tool descriptions for inclusion in system prompts."""
    lines = ["## Available Browser Actions\n"]
    for t in BROWSER_TOOLS:
        lines.append(f"### {t.name}")
        lines.append(f"{t.description}")
        lines.append("")
    return "\n".join(lines)
