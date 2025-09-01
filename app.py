import asyncio
import json
import time
from dataclasses import dataclass, asdict
from typing import Dict, List, Optional, Tuple
from urllib.parse import urlparse

import httpx
import streamlit as st

st.set_page_config(page_title="AI Crawler Access Tester", page_icon="🤖", layout="wide")

# ---- Known AI crawler User-Agents ----
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
    if not parsed.netloc and parsed.path:  # allow bare domains
        return f"https://{parsed.path}"
    return parsed.geturl()


def classify_block(status: int, body_text: str, headers: Dict[str, str]) -> Tuple[str, str]:
    text = (body_text or "")[:2000].lower()
    if status in BLOCK_STATUS:
        return ("true", f"HTTP {status}")
    if any(k in text for k in [
        "access denied", "forbidden", "not authorized", "verify you are human",
        "cloudflare", "akamai", "perimeterx", "attention required"
    ]):
        return ("possible", "Challenge / mitigation text detected")
    if "x-robots-tag" in {k.lower() for k in headers.keys()}:
        return ("false", "x-robots-tag present (informational)")
    return ("false", "OK")


async def fetch_once(client: httpx.AsyncClient, url: str, ua: str, method: str):
    r = await client.request(
        method, url,
        headers={
            "user-agent": ua,
            "accept": "*/*",
            "accept-language": "en",
            "cache-control": "no-cache",
        }
    )
    body = await r.aread() if method == "GET" else b""
    headers = {k: ", ".join(v) if isinstance(v, list) else str(v) for k, v in r.headers.items()}
    body_text = body.decode("utf-8", errors="ignore") if body else ""
    return r.status_code, str(r.url), headers, body_text


async def test_crawler(url: str, crawler: str, ua: str, timeout_s: float = 15.0) -> FetchResult:
    url = normalize_url(url)
    limits = httpx.Limits(max_keepalive_connections=5, max_connections=10)
    async with httpx.AsyncClient(
        follow_redirects=True, http2=True, timeout=timeout_s, limits=limits
    ) as client:
        try:
            with st.spinner(f"Testing {crawler}…"):
                t0 = time.perf_counter()
                try:
                    status, final_url, headers, body = await fetch_once(client, url, ua, "HEAD")
                    if status >= 400 or status == 405:
                        status, final_url, headers, body = await fetch_once(client, url, ua, "GET")
                except httpx.HTTPError:
                    status, final_url, headers, body = await fetch_once(client, url, ua, "GET")
                elapsed_ms = int((time.perf_counter() - t0) * 1000)

                blocked, reason = classify_block(status, body, headers)
                return FetchResult(crawler, url, final_url, status, blocked, reason,
                                   elapsed_ms, headers, body[:500] or "")
        except Exception as e:
            return FetchResult(crawler, url, url, 0, "unknown", f"Fetch failed: {e}",
                               0, {}, "")


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
st.title("🤖 AI Crawler Access Tester")
st.caption("Send requests with popular AI crawler User-Agents to see how your site responds. "
           "This tool does not *enforce* robots.txt; it inspects server responses.")

with st.sidebar:
    st.header("Settings")
    url = st.text_input("Website URL", value="https://example.com")
    chosen_bots = st.multiselect("AI crawlers to simulate", list(CRAWLER_UAS.keys()),
                                 default=["GPTBot", "OAI-SearchBot"])
    timeout = st.slider("Timeout (seconds)", 5, 30, 15)
    show_robots = st.checkbox("Show robots.txt (informational)", value=True)
    run_btn = st.button("Run tests", type="primary")

if "history" not in st.session_state:
    st.session_state.history: List[Dict] = []

if run_btn:
    if not url.strip():
        st.error("Please enter a URL.")
    elif not chosen_bots:
        st.error("Pick at least one crawler.")
    else:
        coros = [test_crawler(url, b, CRAWLER_UAS[b], timeout) for b in chosen_bots]

        async def _run_all():
            return await asyncio.gather(*coros)

        results: List[FetchResult] = asyncio.run(_run_all())
        for r in results:
            st.session_state.history.append(asdict(r))
        st.success("Done!")

# ---- Results ----
if st.session_state.history:
    st.subheader("Results")
    table_rows = list(reversed(st.session_state.history))
    st.dataframe(
        [{
            "Crawler": r["crawler"],
            "Input URL": r["input_url"],
            "Final URL": r["final_url"],
            "HTTP": r["status"],
            "Blocked": r["blocked"],
            "Reason": r["reason"],
            "Elapsed (ms)": r["elapsed_ms"],
        } for r in table_rows],
        use_container_width=True,
        hide_index=True
    )

    st.divider()
    st.subheader("Details")
    for r in table_rows[:20]:
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
                st.write("**Body sample**")
                st.code(r["body_sample"] or "(no body)")

    st.divider()
    st.subheader("Export")
    csv_data = "crawler,input_url,final_url,status,blocked,reason,elapsed_ms\n" + "\n".join(
        f'{r["crawler"]},"{r["input_url"]}","{r["final_url"]}",{r["status"]},{r["blocked"]},"{r["reason"].replace(",", ";")}",{r["elapsed_ms"]}'
        for r in table_rows
    )
    st.download_button("Download CSV", data=csv_data.encode("utf-8"),
                       file_name="crawler_results.csv", mime="text/csv")
    json_data = json.dumps(table_rows, indent=2)
    st.download_button("Download JSON", data=json_data.encode("utf-8"),
                       file_name="crawler_results.json", mime="application/json")

if show_robots and url.strip():
    st.divider()
    st.subheader("robots.txt (informational only)")
    st.code(fetch_robots_txt(url) or "(none)")
