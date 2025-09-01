import asyncio
import json
from dataclasses import dataclass, asdict
from typing import Dict, List, Optional, Tuple
from urllib.parse import urlparse

import httpx
import streamlit as st

st.set_page_config(page_title="AI Crawler Access Tester", page_icon="🤖", layout="wide")

# ---- Known AI crawler User-Agents (you can edit/extend) ----
CRAWLER_UAS: Dict[str, str] = {
    "GPTBot": "Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko; compatible; GPTBot/1.0; +https://openai.com/gptbot)",
    "ChatGPT-User": "Mozilla/5.0 (compatible; ChatGPT-User; +https://openai.com/bot)",
    "OAI-SearchBot": "Mozilla/5.0 (compatible; OAI-SearchBot/1.0; +https://openai.com/searchbot)",
    "PerplexityBot": "Mozilla/5.0 (compatible; PerplexityBot/1.0; +https://www.perplexity.ai/bot)",
    "ClaudeBot": "ClaudeBot/1.0 (+https://www.anthropic.com/claudebot)",
    "Grok": "GrokBot/1.0 (+https://x.ai/grok)",
    "GoogleOther": "GoogleOther",
    "Google-Extended": "Google-Extended",
    "CCBot": "CCBot/2.0 (+https://commoncrawl.org/faq/)",
}

BLOCK_STATUS = {401, 403, 405, 406, 409, 410, 429, 451}

@dataclass
class FetchResult:
    crawler: str
    input_url: str
    final_url: str
    status: int
    blocked: str
    reason: str
    elapsed_ms: int
    headers: Dict[str, str]
    body_sample: str

def normalize_url(u: str) -> str:
    u = u.strip()
    if not u:
        return u
    parsed = urlparse(u, scheme="https")
    # If user entered "example.com" add scheme
    if not parsed.netloc and parsed.path:
        return f"https://{parsed.path}"
    return parsed.geturl()

def classify_block(status: int, body_text: str, headers: Dict[str, str]) -> Tuple[str, str]:
    text = (body_text or "")[:2000]  # limit scan
    lower = text.lower()
    if status in BLOCK_STATUS:
        return ("true", f"HTTP {status}")
    # Heuristics for bot challenges
    if any(k in lower for k in [
        "access denied", "forbidden", "not authorized", "verify you are human",
        "cloudflare", "akamai", "perimeterx", "attention required"
    ]):
        return ("possible", "Challenge / mitigation text detected")
    if "x-robots-tag" in {k.lower() for k in headers.keys()}:
        return ("false", "x-robots-tag present (informational)")
    return ("false", "OK")

async def fetch_once(client: httpx.AsyncClient, url: str, ua: str, method: str) -> Tuple[int, str, Dict[str, str], str]:
    r = await client.request(
        method, url, headers={
            "user-agent": ua,
            "accept": "*/*",
            "accept-language": "en",
            "cache-control": "no-cache",
        }
    )
    body = await r.aread() if method == "GET" else b""
    # Convert headers to str -> str
    headers = {k: ", ".join(v) if isinstance(v, list) else str(v) for k, v in r.headers.items()}
    body_text = ""
    # Best-effort decode as text
    try:
        body_text = body.decode("utf-8", errors="ignore")
    except Exception:
        body_text = ""
    return (r.status_code, str(r.url), headers, body_text)

async def test_crawler(url: str, crawler: str, ua: str, timeout_s: float = 15.0) -> FetchResult:
    url = normalize_url(url)
    limits = httpx.Limits(max_keepalive_connections=5, max_connections=10)
    async with httpx.AsyncClient(
        follow_redirects=True, http2=True, timeout=timeout_s, limits=limits
    ) as client:
        try:
            with st.spinner(f"Testing {crawler}…"):
                # HEAD first (some servers block HEAD; fallback to GET)
                try:
                    status, final_url, headers, body = await fetch_once(client, url, ua, "HEAD")
                    if status >= 400 or status == 405:
                        status, final_url, headers, body = await fetch_once(client, url, ua, "GET")
                except httpx.HTTPError:
                    # Directly try GET on transport errors
                    status, final_url, headers, body = await fetch_once(client, url, ua, "GET")
                blocked, reason = classify_block(status, body, headers)
                elapsed_ms = int(client._transport.handle_request.statistics().total_elapsed * 1000) if hasattr(client._transport, "handle_request") else 0
                return FetchResult(
                    crawler=crawler,
                    input_url=url,
                    final_url=final_url,
                    status=status,
                    blocked=blocked,
                    reason=reason,
                    elapsed_ms=elapsed_ms,
                    headers=headers,
                    body_sample=(body[:500] or "")
                )
        except Exception as e:
            return FetchResult(
                crawler=crawler,
                input_url=url,
                final_url=url,
                status=0,
                blocked="unknown",
                reason=f"Fetch failed: {e}",
                elapsed_ms=0,
                headers={},
                body_sample=""
            )

@st.cache_data(show_spinner=False, ttl=600)
def fetch_robots_txt(base_url: str) -> Optional[str]:
    base = normalize_url(base_url)
    try:
        parsed = urlparse(base)
        robots = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
        r = httpx.get(robots, headers={"user-agent": "Mozilla/5.0"}, timeout=10)
        if r.status_code < 400:
            return r.text[:10000]
        return f"(HTTP {r.status_code})"
    except Exception as e:
        return f"(Error: {e})"

# ---- UI ----
st.title("🤖 AI Crawler Access Tester (Streamlit)")
st.caption("Send requests with popular AI crawler User-Agents to see how your site responds. This tool does not *enforce* robots.txt; it inspects server responses.")

with st.sidebar:
    st.header("Settings")
    default_url = "https://example.com"
    url = st.text_input("Website URL", value=default_url, placeholder="https://yoursite.com")
    chosen_bots = st.multiselect("AI crawlers to simulate", list(CRAWLER_UAS.keys()), default=["GPTBot", "OAI-SearchBot"])
    timeout = st.slider("Timeout (seconds)", 5, 30, 15)
    show_robots = st.checkbox("Show robots.txt (informational)", value=True)
    run_btn = st.button("Run tests", type="primary")

# Initialize history
if "history" not in st.session_state:
    st.session_state.history: List[Dict] = []

if run_btn:
    if not url.strip():
        st.error("Please enter a URL.")
    elif not chosen_bots:
        st.error("Pick at least one crawler.")
    else:
        tasks = [test_crawler(url, b, CRAWLER_UAS[b], timeout) for b in chosen_bots]
        results: List[FetchResult] = asyncio.run(asyncio.gather(*tasks))

        # Append to history (flat dicts for tables/exports)
        for r in results:
            st.session_state.history.append(asdict(r))

        st.success("Done!")

# ---- Results / History ----
if st.session_state.history:
    st.subheader("Results")
    # Show most recent batch on top
    table_rows = list(reversed(st.session_state.history))
    # Compact table
    st.dataframe(
        [{
            "Crawler": r["crawler"],
            "Input URL": r["input_url"],
            "Final URL": r["final_url"],
            "HTTP": r["status"],
            "Blocked": r["blocked"],
            "Reason": r["reason"],
            "Elapsed (ms)": r["elapsed_ms"]
        } for r in table_rows],
        use_container_width=True,
        hide_index=True
    )

    # Detailed per-result expanders
    st.divider()
    st.subheader("Details")
    for r in table_rows[:20]:  # limit expanders for performance
        with st.expander(f'{r["crawler"]} → {r["final_url"]} (HTTP {r["status"]}, blocked={r["blocked"]})'):
            cols = st.columns(3)
            with cols[0]:
                st.write("**Input URL**", r["input_url"])
                st.write("**Final URL**", r["final_url"])
                st.write("**HTTP**", r["status"])
                st.write("**Blocked**", r["blocked"])
                st.write("**Reason**", r["reason"])
                st.write("**Elapsed (ms)**", r["elapsed_ms"])
            with cols[1]:
                st.write("**Response headers**")
                st.json(r["headers"])
            with cols[2]:
                st.write("**Body sample (first 500 chars)**")
                st.code(r["body_sample"] or "(no body)")

    # Exporters
    st.divider()
    st.subheader("Export")
    csv_rows = table_rows
    csv_data = "crawler,input_url,final_url,status,blocked,reason,elapsed_ms\n" + "\n".join(
        f'{r["crawler"]},"{r["input_url"]}","{r["final_url"]}",{r["status"]},{r["blocked"]},"{r["reason"].replace(",", ";")}",{r["elapsed_ms"]}'
        for r in csv_rows
    )
    st.download_button("Download CSV", data=csv_data.encode("utf-8"), file_name="crawler_results.csv", mime="text/csv")

    json_data = json.dumps(table_rows, indent=2)
    st.download_button("Download JSON", data=json_data.encode("utf-8"), file_name="crawler_results.json", mime="application/json")

# robots.txt (optional, informational)
if show_robots and url.strip():
    st.divider()
    st.subheader("robots.txt (informational only)")
    st.caption("Shown for context—this app does not interpret robots.txt to decide blocked/allowed.")
    st.code(fetch_robots_txt(url) or "(none)")
